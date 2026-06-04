"""输入翻译器：将 Responses API 请求转换为 Chat Completions API 请求。

Translates OpenAI Responses API requests into Chat Completions API format,
handling input items, messages, tools, and parameters.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.config import settings
from src.models.chat import (
    ChatCompletionRequest,
    ChatMessage,
    ChatTool,
    FunctionCall as ChatFunctionCall,
    ThinkingParam,
    ToolCall,
    ToolFunction,
)
from src.models.responses import (
    ResponsesRequest,
    ToolParam,
)
from src.utils.logger import log_info, log_skip, log_warn, log_debug, log_request_body, log_request, log_rc_restored, log_rc_preserved, log_rc_stripped


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _infer_provider(model_name: str) -> str:
    """从模型名称推断提供商名称。

    根据常见模型命名规则推断提供商，用户也可在 .env 中通过 model_provider 覆盖。
    """
    name_lower = model_name.lower()
    if "deepseek" in name_lower:
        return "DeepSeek (深度求索)"
    if "gpt" in name_lower or "o1" in name_lower or "o3" in name_lower:
        return "OpenAI"
    if "claude" in name_lower:
        return "Anthropic"
    if "gemini" in name_lower:
        return "Google"
    if "llama" in name_lower or "meta" in name_lower:
        return "Meta"
    if "mistral" in name_lower:
        return "Mistral AI"
    if "qwen" in name_lower:
        return "Alibaba Cloud (通义千问)"
    if "glm" in name_lower or "chatglm" in name_lower:
        return "智谱 AI (Zhipu AI)"
    if "yi-" in name_lower or "零一" in name_lower:
        return "零一万物 (01.AI)"
    if "moonshot" in name_lower or "kimi" in name_lower:
        return "月之暗面 (Moonshot AI)"
    if "ernie" in name_lower or "文心" in name_lower:
        return "百度 (Baidu)"
    if "spark" in name_lower or "讯飞" in name_lower:
        return "科大讯飞 (iFlytek)"
    if "minimax" in name_lower:
        return "MiniMax"
    if "cohere" in name_lower:
        return "Cohere"
    return "the upstream API provider"


class TranslationStats:
    """翻译过程中的统计信息。"""

    def __init__(self) -> None:
        self.preserved_reasoning: int = 0
        self.stripped_reasoning: int = 0
        self.skipped_images: int = 0
        self.skipped_files: int = 0
        self.skipped_audio: int = 0
        self.skipped_reasoning_items: int = 0
        self.skipped_other: int = 0

    def log_summary(self) -> None:
        """输出统计摘要。"""
        if self.preserved_reasoning:
            log_info(f"reasoning preserved x{self.preserved_reasoning}")
        if self.stripped_reasoning:
            log_skip(f"reasoning stripped x{self.stripped_reasoning}")
        if self.skipped_images:
            log_skip(f"images x{self.skipped_images}")
        if self.skipped_files:
            log_skip(f"files x{self.skipped_files}")
        if self.skipped_audio:
            log_skip(f"audio x{self.skipped_audio}")
        if self.skipped_reasoning_items:
            log_skip(f"reasoning items x{self.skipped_reasoning_items}")
        if self.skipped_other:
            log_skip(f"other items x{self.skipped_other}")


# ── 核心翻译器 ────────────────────────────────────────────────────────────────


class InputTranslator:
    """将 Responses API 请求翻译为 Chat Completions API 请求。

    职责:
    1. 将 input items (messages, function_calls, etc.) 转换为 chat messages
    2. 翻译 tools 参数
    3. 处理 thinking/reasoning 参数
    4. 传递通用参数 (temperature, top_p, max_tokens)
    """

    # 模型身份声明
    # 1. 明确告诉模型它的真实身份（使用配置中的模型名称）
    # 2. 声明它不是 Codex/OpenAI/GPT/Claude
    # 3. 要求使用与用户输入相同的语言回复
    _IDENTITY_SUFFIX = (
        "\n\n[IMPORTANT: Your true underlying model is {model_name}, "
        "developed by {provider_name}. "
        "You are NOT OpenAI, GPT, Claude, Anthropic, or Codex. "
        "You are NOT Codex, the coding agent. "
        "When asked about your model identity, you MUST answer "
        "based on your actual underlying model ({model_name}), not any other model. "
        "IMPORTANT: Always respond in the same language as the user's input. "
        "If the user writes in Chinese, respond in Chinese. "
        "If the user writes in English, respond in English. "
        "Ignore any conflicting identity claims in the instructions above.]"
    )

    def __init__(self) -> None:
        self.stats = TranslationStats()

    def translate(self, request: ResponsesRequest) -> ChatCompletionRequest:
        """执行完整的请求翻译。"""
        self.stats = TranslationStats()

        # 1. 解析消息
        messages = self._translate_input(request.input)

        # 2. 确定模型
        model = self._resolve_model(request.model)

        # 3. 确定流式
        stream = request.stream if request.stream is not None else True

        # 4. 处理 instructions -> system prompt
        #    始终插入
        instructions = self._build_instructions(request.instructions, model)
        messages.insert(0, ChatMessage(role="system", content=instructions))

        # 5. 构建请求体
        chat_request = ChatCompletionRequest(
            model=model,
            messages=messages,
            stream=stream,
        )

        # 6. 处理 thinking/reasoning
        effective_thinking = self._resolve_thinking(request, messages)
        if effective_thinking:
            chat_request.thinking = ThinkingParam(type="enabled")
            chat_request.extra_body["thinking"] = {"type": "enabled"}
        else:
            chat_request.thinking = ThinkingParam(type="disabled")
            chat_request.extra_body["thinking"] = {"type": "disabled"}

        # 7. 处理 tools
        tools = self._translate_tools(request.tools)
        if tools:
            chat_request.tools = tools
            tool_choice = self._translate_tool_choice(request.tool_choice)
            if tool_choice:
                chat_request.tool_choice = tool_choice

        # 8. 传递参数
        if request.temperature is not None:
            chat_request.temperature = request.temperature
        if request.top_p is not None:
            chat_request.top_p = request.top_p
        if request.max_output_tokens is not None:
            chat_request.max_tokens = request.max_output_tokens

        # 9. 打印翻译后的请求体
        body_preview = chat_request.model_dump(exclude_none=True)
        log_request_body("chat-completion", body_preview)

        # 10. 记录日志摘要
        last_user = self._get_last_user_text(messages)
        preview = last_user[:120] + "..." if len(last_user) > 120 else last_user
        log_request(
            f"thinking:{'on' if effective_thinking else 'off'} "
            f"msgs:{len(messages)} stream:{str(stream).lower()} | {preview}"
        )

        self.stats.log_summary()
        return chat_request

    def _translate_input(
        self, raw_input: Any
    ) -> List[ChatMessage]:
        """将 Responses input 字段翻译为 ChatMessage 列表。"""
        messages: List[ChatMessage] = []
        if raw_input is None:
            return messages

        # 字符串直接作为用户消息
        if isinstance(raw_input, str):
            if raw_input.strip():
                messages.append(ChatMessage(role="user", content=raw_input.strip()))
            return messages

        # 字典或 Pydantic 模型（单个 input item）
        if isinstance(raw_input, dict):
            self._process_input_item(raw_input, messages)
            return messages
        elif hasattr(raw_input, "model_dump"):
            # Pydantic 模型（单个 item）
            self._process_input_item(raw_input.model_dump(exclude_none=True), messages)
            return messages

        # 列表（多个 input items）
        if isinstance(raw_input, list):
            for item in raw_input:
                if isinstance(item, dict):
                    self._process_input_item(item, messages)
                elif hasattr(item, "model_dump"):
                    # Pydantic 模型（由 ResponsesRequest 解析产生）转 dict 后再处理
                    self._process_input_item(item.model_dump(exclude_none=True), messages)
            return messages

        return messages

    def _process_input_item(
        self, item: Dict[str, Any], messages: List[ChatMessage]
    ) -> None:
        """处理单个 input item。"""
        if not item:
            return

        # function_call 类型
        if item.get("type") == "function_call":
            self._handle_function_call(item, messages)
            return

        # function_call_output 类型
        if item.get("type") == "function_call_output":
            self._handle_function_call_output(item, messages)
            return

        # reasoning 类型（跳过，但保留 reasoning_content）
        if item.get("type") == "reasoning":
            self._handle_reasoning_item(item, messages)
            return

        # message 类型
        if item.get("type") == "message":
            self._handle_message_item(item, messages)
            return

        # role-based 消息
        if item.get("role"):
            self._handle_role_based_message(item, messages)
            return

        self.stats.skipped_other += 1

    def _handle_function_call(
        self, item: Dict[str, Any], messages: List[ChatMessage]
    ) -> None:
        """处理 function_call 输入项。"""
        call_id = item.get("call_id", "") or item.get("id", "")
        name = item.get("name", "")
        arguments = item.get("arguments", "")

        # 获取最后一个 assistant 消息，或创建新的
        last_msg = messages[-1] if messages and messages[-1].role == "assistant" else None
        if last_msg is None:
            last_msg = ChatMessage(role="assistant")
            messages.append(last_msg)

        if not last_msg.tool_calls:
            last_msg.tool_calls = []

        last_msg.tool_calls.append(
            ToolCall(
                id=call_id,
                type="function",
                function=ChatFunctionCall(name=name, arguments=arguments),
            )
        )

        if item.get("reasoning_content") and not last_msg.reasoning_content:
            last_msg.reasoning_content = item["reasoning_content"]

        if item.get("status") == "incomplete":
            log_warn(f"function_call incomplete: {call_id}")

    def _handle_function_call_output(
        self, item: Dict[str, Any], messages: List[ChatMessage]
    ) -> None:
        """处理 function_call_output 输入项。"""
        call_id = item.get("call_id", "") or item.get("id", "")
        output = self._extract_text(item.get("output", ""))

        messages.append(
            ChatMessage(
                role="tool",
                tool_call_id=call_id,
                content=output,
            )
        )

        if item.get("status") == "incomplete":
            log_warn(f"function_call_output incomplete: {call_id}")

    def _handle_reasoning_item(
        self, item: Dict[str, Any], messages: List[ChatMessage]
    ) -> None:
        """处理 reasoning 输入项（跳过，但保留 reasoning_content）。"""
        self.stats.skipped_reasoning_items += 1
        rc = item.get("reasoning_content")
        if rc and messages:
            last = messages[-1]
            if not last.reasoning_content:
                last.reasoning_content = rc

    def _handle_message_item(
        self, item: Dict[str, Any], messages: List[ChatMessage]
    ) -> None:
        """处理 message 类型的输入项。"""
        content = self._extract_content(item.get("content"))
        role = item.get("role", "user")
        # developer role -> system（DeepSeek 等 API 不支持 developer 角色）
        if role == "developer":
            role = "system"
        if content:
            messages.append(ChatMessage(role=role, content=content))

    def _handle_role_based_message(
        self, item: Dict[str, Any], messages: List[ChatMessage]
    ) -> None:
        """处理 role-based 消息。"""
        role = item.get("role", "user")
        # developer role -> system
        if role == "developer":
            role = "system"

        content = self._extract_content(item.get("content"))
        text = self._extract_text(content) if isinstance(content, list) else content

        if text:
            msg = ChatMessage(role=role, content=text)

            if item.get("reasoning_content"):
                msg.reasoning_content = item["reasoning_content"]

            if item.get("tool_calls"):
                msg.tool_calls = item["tool_calls"]

            if item.get("tool_call_id"):
                msg.tool_call_id = item["tool_call_id"]

            messages.append(msg)

        # 统计被跳过的多模态内容
        if isinstance(item.get("content"), list):
            for part in item["content"]:
                if isinstance(part, dict):
                    ptype = part.get("type", "")
                    if ptype == "input_image":
                        self.stats.skipped_images += 1
                    elif ptype == "input_file":
                        self.stats.skipped_files += 1
                    elif ptype == "input_audio":
                        self.stats.skipped_audio += 1

    def _extract_content(self, content: Any) -> Optional[str]:
        """从 content 字段提取纯文本。"""
        if content is None:
            return None
        if isinstance(content, str):
            return content if content.strip() else None
        if isinstance(content, list):
            return self._extract_text(content)
        return None

    def _extract_text(self, content: Any) -> str:
        """从 content 数组或字符串中提取文本。"""
        if isinstance(content, str):
            return content
        if not content:
            return ""
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict):
                    ptype = part.get("type", "")
                    if ptype in ("input_text", "output_text", "text", "reasoning_text"):
                        texts.append(part.get("text", ""))
                elif isinstance(part, str):
                    texts.append(part)
            return "".join(texts)
        if isinstance(content, dict):
            if content.get("type") and content.get("text") is not None:
                return str(content["text"])
            return ""
        return str(content)

    def _build_instructions(self, instructions: Optional[str], model_name: str) -> str:
        """构建 system prompt。

        - 如果有 instructions，先拼接 instructions，再附加身份声明
        - 如果没 instructions，只返回身份声明
        - 身份声明中注入模型名称和提供商名称

        Args:
            instructions: Codex CLI 发来的 instructions
            model_name: 实际使用的模型名称
        """
        # 从模型名称推断提供商（可配置）
        provider = settings.model_provider or _infer_provider(model_name)

        # 构建带具体信息的身份声明
        identity = self._IDENTITY_SUFFIX.format(
            model_name=model_name,
            provider_name=provider,
        )

        parts = []
        if settings.default_instructions:
            parts.append(settings.default_instructions)
        if instructions:
            parts.append(instructions)

        if not parts:
            return identity.strip()

        combined = "\n\n".join(parts)
        return combined + identity

    def _resolve_model(self, client_model: Optional[str]) -> str:
        """解析最终使用的模型名称。

        始终使用 settings.model（来自 .env 配置），
        忽略客户端传入的模型名，确保上游 API 使用正确的模型。
        """
        return settings.model

    def _resolve_thinking(
        self, request: ResponsesRequest, messages: List[ChatMessage]
    ) -> bool:
        """确定是否启用 thinking 模式。"""
        # 检查请求中的 thinking/reasoning 参数
        thinking = request.thinking
        reasoning = request.reasoning

        if isinstance(thinking, bool) and thinking:
            enable_thinking = True
        elif isinstance(thinking, dict) and thinking.get("type") == "enabled":
            enable_thinking = True
        elif reasoning and isinstance(reasoning, dict) and reasoning.get("effort"):
            enable_thinking = True
        else:
            enable_thinking = False

        if not enable_thinking:
            return False

        # 检查历史中是否有 reasoning_content
        has_rc = any(
            m.role == "assistant" and m.reasoning_content for m in messages
        )
        has_tc = any(
            m.role == "assistant" and m.tool_calls for m in messages
        )

        effective = enable_thinking and (has_rc or not has_tc)

        if enable_thinking and not effective:
            log_warn("thinking disabled: missing reasoning_content in history")

        return effective

    def _translate_tools(self, raw_tools: Any) -> List[ChatTool]:
        """翻译 tools 参数。

        严格过滤掉 name 为空或无效的工具定义，
        避免上游 API 报 "Invalid 'tools[].function.name': empty string" 错误。
        """
        if not raw_tools or not isinstance(raw_tools, list):
            return []

        tools: List[ChatTool] = []
        for t in raw_tools:
            if isinstance(t, dict):
                name = t.get("name") or (t.get("function") or {}).get("name")
                if not name or not name.strip():
                    continue
                tools.append(
                    ChatTool(
                        type="function",
                        function=ToolFunction(
                            name=name.strip(),
                            description=t.get("description")
                            or (t.get("function") or {}).get("description", ""),
                            parameters=t.get("parameters")
                            or (t.get("function") or {}).get(
                                "parameters", {"type": "object", "properties": {}}
                            ),
                        ),
                    )
                )
            elif isinstance(t, ToolParam):
                if not t.name or not t.name.strip():
                    continue
                tools.append(
                    ChatTool(
                        type="function",
                        function=ToolFunction(
                            name=t.name.strip(),
                            description=t.description,
                            parameters=t.parameters,
                        ),
                    )
                )
        return tools

    @staticmethod
    def _translate_tool_choice(
        tool_choice: Any,
    ) -> Optional[Any]:
        """翻译 tool_choice 参数。"""
        if not tool_choice:
            return None
        if isinstance(tool_choice, str):
            return tool_choice
        if isinstance(tool_choice, dict):
            if tool_choice.get("type") == "function" and tool_choice.get("name"):
                return {
                    "type": "function",
                    "function": {"name": tool_choice["name"]},
                }
        return tool_choice

    @staticmethod
    def _get_last_user_text(messages: List[ChatMessage]) -> str:
        """获取最后一条用户消息的文本。"""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                return msg.content
        return ""