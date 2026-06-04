"""SSE 流式翻译器：将 Chat Completions 流式事件实时翻译为 Responses API SSE 事件。

Translates streaming Chat Completions API chunks into OpenAI Responses API
Server-Sent Events in real time.
"""

from __future__ import annotations

import json
import random
import string
from typing import Any, Dict, List, Optional

from src.models.chat import ChatCompletionChunk, StreamChoice
from src.utils.logger import log_info, log_ok, log_error, log_token, log_request, log_response


def _random_id(prefix: str = "", length: int = 8) -> str:
    """生成随机 ID。"""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return f"{prefix}{suffix}"


class SseTranslator:
    """将 Chat Completions 流式 Chunks 转换为 Responses API SSE 事件。

    生成的 SSE 事件格式与 OpenAI Responses API 完全兼容，
    包含完整的 response lifecycle 事件序列。

    事件序列:
    1. response.created
    2. response.in_progress
    3. (for each output item:)
       - response.output_item.added
       - response.content_part.added
       - response.output_text.delta / response.reasoning_text.delta
       - response.function_call_arguments.delta
    4. (for each output item:)
       - response.content_part.done
       - response.output_text.done / response.reasoning_text.done
       - response.function_call_arguments.done
       - response.output_item.done
    5. response.completed
    """

    def __init__(self) -> None:
        self.response_id = _random_id("resp_", 8)
        self.message_item_id = _random_id("item_", 8)

        self.text_started = False
        self.content_so_far = ""
        self.reasoning_started = False
        self.reasoning_so_far = ""
        self.reasoning_item_id: Optional[str] = None

        self.tool_calls: Dict[int, Dict[str, Any]] = {}
        self.output_items: List[Dict[str, Any]] = []
        self.output_item_count = 0
        self._last_usage: Optional[Dict[str, Any]] = None

    def _sse_start_log(self) -> None:
        """记录 SSE start 日志（只在首次调用时记录）。"""
        if not hasattr(self, '_sse_started_log'):
            self._sse_started_log = True
            log_info(f"SSE start: {self.response_id}")

    def translate_chunk(self, chunk: ChatCompletionChunk) -> List[Dict[str, Any]]:
        """翻译一个 Chat 流式 Chunk 为一组 SSE 事件。

        Args:
            chunk: 一个流式响应 chunk。

        Returns:
            该 chunk 对应的事件列表，每个事件为 {"event": str, "data": dict}。
        """
        events: List[Dict[str, Any]] = []

        if not chunk.choices:
            # 可能是 usage-only chunk
            if chunk.usage:
                self._last_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
            return events

        choice = chunk.choices[0]
        delta = choice.delta
        if not delta:
            return events

        # 记录 usage
        if chunk.usage:
            self._last_usage = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
            }

        # 处理文本内容
        if delta.content:
            self.content_so_far += delta.content
            events.extend(self._emit_text_delta(delta.content))

        # 处理推理内容
        if delta.reasoning_content:
            self.reasoning_so_far += delta.reasoning_content
            events.extend(self._emit_reasoning_delta(delta.reasoning_content))

        # 处理工具调用
        if delta.tool_calls:
            for tc in delta.tool_calls:
                events.extend(self._emit_tool_call_delta(tc))

        return events

    def translate_done(self, model: str) -> List[Dict[str, Any]]:
        """翻译完成事件。

        生成所有 output item 的 done events 以及最终的 response.completed 事件。

        Args:
            model: 使用的模型名称。

        Returns:
            完成阶段的事件列表。
        """
        events: List[Dict[str, Any]] = []
        usage = self._last_usage

        # 消息完成
        if self.text_started:
            oi = self._msg_index()
            part = {"type": "output_text", "text": self.content_so_far, "annotations": []}
            events.append({
                "event": "response.content_part.done",
                "data": {
                    "type": "response.content_part.done",
                    "response_id": self.response_id,
                    "item_id": self.message_item_id,
                    "output_index": oi,
                    "content_index": 0,
                    "part": part,
                },
            })
            events.append({
                "event": "response.output_text.done",
                "data": {
                    "type": "response.output_text.done",
                    "response_id": self.response_id,
                    "item_id": self.message_item_id,
                    "output_index": oi,
                    "content_index": 0,
                    "text": self.content_so_far,
                },
            })
            events.append({
                "event": "response.output_item.done",
                "data": {
                    "type": "response.output_item.done",
                    "response_id": self.response_id,
                    "output_index": oi,
                    "item": {
                        "id": self.message_item_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [part],
                        "status": "completed",
                    },
                },
            })
            log_response(f"text output: {len(self.content_so_far)} chars")

        # 推理完成
        if self.reasoning_started:
            oi = self._rsn_index()
            events.append({
                "event": "response.content_part.done",
                "data": {
                    "type": "response.content_part.done",
                    "response_id": self.response_id,
                    "item_id": self.reasoning_item_id,
                    "output_index": oi,
                    "content_index": 0,
                    "part": {"type": "reasoning_text", "text": self.reasoning_so_far},
                },
            })
            events.append({
                "event": "response.reasoning_text.done",
                "data": {
                    "type": "response.reasoning_text.done",
                    "response_id": self.response_id,
                    "item_id": self.reasoning_item_id,
                    "output_index": oi,
                    "content_index": 0,
                    "text": self.reasoning_so_far,
                },
            })
            events.append({
                "event": "response.output_item.done",
                "data": {
                    "type": "response.output_item.done",
                    "response_id": self.response_id,
                    "output_index": oi,
                    "item": {
                        "id": self.reasoning_item_id,
                        "type": "reasoning",
                        "content": [{"type": "reasoning_text", "text": self.reasoning_so_far}],
                        "status": "completed",
                    },
                },
            })
            log_response(f"reasoning output: {len(self.reasoning_so_far)} chars")

        # 工具调用完成
        for idx, call in self.tool_calls.items():
            oi = self._item_index("fc_" + call["id"])
            out_idx = oi if oi >= 0 else idx + 1
            events.append({
                "event": "response.function_call_arguments.done",
                "data": {
                    "type": "response.function_call_arguments.done",
                    "response_id": self.response_id,
                    "item_id": "fc_" + call["id"],
                    "output_index": out_idx,
                    "arguments": call["arguments"],
                    "name": call["name"],
                    "call_id": call["id"],
                },
            })
            events.append({
                "event": "response.output_item.done",
                "data": {
                    "type": "response.output_item.done",
                    "response_id": self.response_id,
                    "output_index": out_idx,
                    "item": {
                        "id": "fc_" + call["id"],
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                        "status": "completed",
                    },
                },
            })
            log_response(f"tool done: {call['name']}")

        # 构建输出快照
        resp_usage = None
        if usage:
            resp_usage = {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }

        out_snapshot = self._build_output_snapshot()

        events.append({
            "event": "response.completed",
            "data": {
                "type": "response.completed",
                "response": {
                    "id": self.response_id,
                    "object": "response",
                    "status": "completed",
                    "model": model,
                    "output": out_snapshot,
                    "usage": resp_usage,
                },
            },
        })

        if usage:
            log_token(
                prompt=usage.get("prompt_tokens", 0),
                completion=usage.get("completion_tokens", 0),
                total=usage.get("total_tokens", 0),
            )

        log_ok(f"SSE done: {self.response_id}")
        return events

    def translate_error(self, message: str) -> List[Dict[str, Any]]:
        """翻译错误事件。"""
        log_error(f"SSE error: {message}")
        return [
            {
                "event": "error",
                "data": {
                    "type": "error",
                    "code": "proxy_error",
                    "message": message,
                },
            }
        ]

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _ensure_started(self) -> List[Dict[str, Any]]:
        """确保 response 已开始，返回起始事件。"""
        events: List[Dict[str, Any]] = []
        # 延迟初始化 - 每次 emit 事件时才检查
        return events

    def _start_events(self) -> List[Dict[str, Any]]:
        """生成 response 起始事件。"""
        return [
            {
                "event": "response.created",
                "data": {
                    "type": "response.created",
                    "response": {
                        "id": self.response_id,
                        "object": "response",
                        "status": "in_progress",
                        "model": "",
                        "output": [],
                    },
                },
            },
            {
                "event": "response.in_progress",
                "data": {
                    "type": "response.in_progress",
                    "response_id": self.response_id,
                },
            },
        ]

    def _emit_text_delta(self, delta: str) -> List[Dict[str, Any]]:
        """生成文本增量事件。"""
        events: List[Dict[str, Any]] = []

        if not self.text_started:
            # 首次接收文本，添加起始事件
            self._sse_start_log()
            events.extend(self._start_events())
            self.text_started = True
            oi = self.output_item_count
            self.output_item_count += 1
            self.output_items.append({"index": oi, "type": "message", "itemId": self.message_item_id})

            events.append({
                "event": "response.output_item.added",
                "data": {
                    "type": "response.output_item.added",
                    "response_id": self.response_id,
                    "output_index": oi,
                    "item": {
                        "id": self.message_item_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "in_progress",
                        "content": [],
                    },
                },
            })
            events.append({
                "event": "response.content_part.added",
                "data": {
                    "type": "response.content_part.added",
                    "response_id": self.response_id,
                    "item_id": self.message_item_id,
                    "output_index": oi,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
            })

        # 继续输出增量
        oi = self._msg_index()
        events.append({
            "event": "response.output_text.delta",
            "data": {
                "type": "response.output_text.delta",
                "response_id": self.response_id,
                "item_id": self.message_item_id,
                "output_index": oi,
                "content_index": 0,
                "delta": delta,
            },
        })

        return events

    def _emit_reasoning_delta(self, delta: str) -> List[Dict[str, Any]]:
        """生成推理内容增量事件。"""
        events: List[Dict[str, Any]] = []

        if not self.reasoning_started:
            self._sse_start_log()
            events.extend(self._start_events())
            self.reasoning_started = True
            self.reasoning_item_id = _random_id("rsn_", 6)
            oi = self.output_item_count
            self.output_item_count += 1
            self.output_items.append({"index": oi, "type": "reasoning", "itemId": self.reasoning_item_id})

            events.append({
                "event": "response.output_item.added",
                "data": {
                    "type": "response.output_item.added",
                    "response_id": self.response_id,
                    "output_index": oi,
                    "item": {
                        "id": self.reasoning_item_id,
                        "type": "reasoning",
                        "status": "in_progress",
                        "summary": [],
                    },
                },
            })
            events.append({
                "event": "response.content_part.added",
                "data": {
                    "type": "response.content_part.added",
                    "response_id": self.response_id,
                    "item_id": self.reasoning_item_id,
                    "output_index": oi,
                    "content_index": 0,
                    "part": {"type": "reasoning_text", "text": ""},
                },
            })

        rsn_idx = self._rsn_index()
        if rsn_idx >= 0:
            events.append({
                "event": "response.reasoning_text.delta",
                "data": {
                    "type": "response.reasoning_text.delta",
                    "response_id": self.response_id,
                    "item_id": self.reasoning_item_id,
                    "output_index": rsn_idx,
                    "content_index": 0,
                    "delta": delta,
                },
            })

        return events

    @staticmethod
    def _tc_get(tc: Any, key: str, default: Any = None) -> Any:
        """安全地从 ToolCall 对象或 dict 中获取属性。"""
        if isinstance(tc, dict):
            return tc.get(key, default)
        return getattr(tc, key, default)

    @staticmethod
    def _tc_get_nested(tc: Any, *keys: str) -> Any:
        """从 ToolCall 对象或 dict 中安全地获取嵌套属性。"""
        current = tc
        for key in keys:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
        return current

    def _emit_tool_call_delta(self, tc: Any) -> List[Dict[str, Any]]:
        """生成工具调用增量事件。

        兼容 ToolCall Pydantic 对象和原始 dict 两种输入格式。
        """
        events: List[Dict[str, Any]] = []
        idx = self._tc_get(tc, "index", 0)

        if idx not in self.tool_calls:
            self._sse_start_log()
            events.extend(self._start_events())
            call_id = self._tc_get(tc, "id", "") or _random_id("call_", 6)
            name = self._tc_get_nested(tc, "function", "name") or ""
            self.tool_calls[idx] = {
                "id": call_id,
                "name": name,
                "arguments": "",
            }
            oi = self.output_item_count
            self.output_item_count += 1
            self.output_items.append({"index": oi, "type": "function_call", "itemId": "fc_" + call_id})

            events.append({
                "event": "response.output_item.added",
                "data": {
                    "type": "response.output_item.added",
                    "response_id": self.response_id,
                    "output_index": oi,
                    "item": {
                        "id": "fc_" + call_id,
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "status": "in_progress",
                    },
                },
            })
            log_request(f"tool: {name} ({call_id})")

        call = self.tool_calls[idx]
        new_name = self._tc_get_nested(tc, "function", "name")
        if new_name:
            call["name"] = new_name
        arg_delta = self._tc_get_nested(tc, "function", "arguments") or ""
        call["arguments"] += arg_delta

        oi = self._item_index("fc_" + call["id"])
        if oi >= 0:
            events.append({
                "event": "response.function_call_arguments.delta",
                "data": {
                    "type": "response.function_call_arguments.delta",
                    "response_id": self.response_id,
                    "item_id": "fc_" + call["id"],
                    "output_index": oi,
                    "delta": arg_delta,
                },
            })

        return events

    def _build_output_snapshot(self) -> List[Dict[str, Any]]:
        """构建最终响应中的 output 快照。"""
        snapshot = []
        for o in self.output_items:
            if o["type"] == "message":
                snapshot.append({
                    "id": o["itemId"],
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": self.content_so_far, "annotations": []}],
                    "status": "completed",
                })
            elif o["type"] == "reasoning":
                snapshot.append({
                    "id": o["itemId"],
                    "type": "reasoning",
                    "content": [{"type": "reasoning_text", "text": self.reasoning_so_far}],
                    "status": "completed",
                })
            elif o["type"] == "function_call":
                for _, c in self.tool_calls.items():
                    if "fc_" + c["id"] == o["itemId"]:
                        snapshot.append({
                            "id": o["itemId"],
                            "type": "function_call",
                            "call_id": c["id"],
                            "name": c["name"],
                            "arguments": c["arguments"],
                            "status": "completed",
                        })
        return snapshot

    def _msg_index(self) -> int:
        for o in self.output_items:
            if o["type"] == "message":
                return o["index"]
        return 0

    def _rsn_index(self) -> int:
        for o in self.output_items:
            if o["type"] == "reasoning":
                return o["index"]
        return -1

    def _item_index(self, item_id: str) -> int:
        for o in self.output_items:
            if o["itemId"] == item_id:
                return o["index"]
        return -1