"""输出翻译器：将 Chat Completions API 响应转换为 Responses API 格式（非流式）。

Converts non-streaming Chat Completions API responses into the 
OpenAI Responses API response format.
"""

from __future__ import annotations

import random
import string
from typing import Any, Dict, List, Optional

from src.models.chat import ChatCompletionResponse, UsageInfo
from src.models.responses import (
    OutputTextPart,
    ReasoningTextPart,
    ResponseFunctionCallItem,
    ResponseMessageItem,
    ResponseReasoningItem,
    ResponseUsage,
    ResponsesResponse,
)


def _random_id(prefix: str = "", length: int = 8) -> str:
    """生成随机 ID。"""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return f"{prefix}{suffix}"


class OutputTranslator:
    """将非流式 Chat Completions 响应转换为 Responses API 响应。"""

    def translate(
        self, completion: ChatCompletionResponse, model: str
    ) -> ResponsesResponse:
        """执行翻译。"""
        choice = completion.choices[0] if completion.choices else None
        if not choice:
            return self._empty_response(model)

        msg = choice.message
        output: List[Any] = []

        # reasoning_content -> reasoning output item
        if msg.reasoning_content:
            rsn_id = _random_id("rsn_", 6)
            output.append(
                ResponseReasoningItem(
                    id=rsn_id,
                    type="reasoning",
                    status="completed",
                    content=[ReasoningTextPart(text=msg.reasoning_content)],
                )
            )

        # content -> message output item
        if msg.content:
            msg_id = _random_id("msg_", 6)
            output.append(
                ResponseMessageItem(
                    id=msg_id,
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[OutputTextPart(text=msg.content)],
                )
            )

        # tool_calls -> function_call output items
        if msg.tool_calls:
            for tc in msg.tool_calls:
                fc_id = _random_id("fc_", 6)
                output.append(
                    ResponseFunctionCallItem(
                        id=fc_id,
                        type="function_call",
                        call_id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                        status="completed",
                    )
                )

        # usage
        usage = None
        if completion.usage:
            usage = ResponseUsage(
                input_tokens=completion.usage.prompt_tokens,
                output_tokens=completion.usage.completion_tokens,
                total_tokens=completion.usage.total_tokens,
            )

        return ResponsesResponse(
            id=_random_id("resp_", 8),
            object="response",
            status="completed",
            model=model,
            output=output,
            usage=usage,
        )

    @staticmethod
    def _empty_response(model: str) -> ResponsesResponse:
        """生成空响应。"""
        return ResponsesResponse(
            id=_random_id("resp_", 8),
            object="response",
            status="completed",
            model=model,
            output=[],
            usage=None,
        )