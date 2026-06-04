"""上游 API 客户端：向 Chat Completions API 发送请求并获取响应。

Uses httpx for async HTTP communication with upstream LLM providers.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator, Any, Dict, Optional

import httpx

from src.config import settings
from src.models.chat import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    StreamChoice,
    DeltaMessage,
    UsageInfo,
)
from src.utils.logger import log_info, log_error, log_debug, log_request_body, log_response_body


class UpstreamClient:
    """与上游 Chat Completions API 通信的异步客户端。"""

    def __init__(self) -> None:
        self._base_url = settings.base_url.rstrip("/")
        self._api_key = settings.api_key
        self._timeout = settings.request_timeout
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._http.aclose()

    async def chat_completions(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """发送非流式 Chat Completions 请求。

        Args:
            request: 翻译后的 Chat Completions 请求。

        Returns:
            上游 API 的完整响应。
        """
        url = f"{self._base_url}/chat/completions"
        body = self._build_request_body(request)

        log_debug(f"upstream POST {url} (non-stream)")
        log_request_body("upstream", body)

        response = await self._http.post(
            url,
            json=body,
            headers=self._headers(),
        )

        if response.status_code != 200:
            error_text = response.text[:500]
            log_error(
                f"upstream error {response.status_code}: {error_text}"
            )
            response.raise_for_status()

        data = response.json()
        log_response_body("upstream", data)
        return self._parse_completion(data, request.model)

    async def chat_completions_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        """发送流式 Chat Completions 请求，逐 chunk 产出。

        Args:
            request: 翻译后的 Chat Completions 请求。

        Yields:
            每个流式响应的 ChatCompletionChunk。
        """
        url = f"{self._base_url}/chat/completions"
        body = self._build_request_body(request)

        log_debug(f"upstream POST {url} (stream)")
        log_request_body("upstream-stream", body)

        async with self._http.stream(
            "POST",
            url,
            json=body,
            headers=self._headers(),
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                error_msg = error_text.decode("utf-8", errors="replace")[:500]
                log_error(
                    f"upstream error {response.status_code}: {error_msg}"
                )
                response.raise_for_status()

            buffer = ""
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue

                json_str = raw_line[6:].strip()
                if json_str == "[DONE]":
                    break

                try:
                    chunk_data = json.loads(json_str)
                    chunk = self._parse_chunk(chunk_data, request.model)
                    if chunk:
                        yield chunk
                except json.JSONDecodeError as e:
                    log_debug(f"skip invalid JSON: {e}")
                    continue

    def _headers(self) -> Dict[str, str]:
        """构建请求头。"""
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
            headers["Accept"] = "application/json"
        return headers

    def _build_request_body(self, request: ChatCompletionRequest) -> Dict[str, Any]:
        """将 ChatCompletionRequest 构建为 JSON 请求体。"""
        body: Dict[str, Any] = {
            "model": request.model,
            "messages": [msg.model_dump(exclude_none=True) for msg in request.messages],
            "stream": request.stream,
        }

        # 可选参数
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.tools is not None:
            body["tools"] = [
                t.model_dump(exclude_none=True) for t in request.tools
            ]
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice
        if request.thinking is not None:
            body["thinking"] = request.thinking.model_dump()

        # 扩展字段
        if request.extra_body:
            body.update(request.extra_body)

        return body

    def _parse_completion(
        self, data: Dict[str, Any], model: str
    ) -> ChatCompletionResponse:
        """解析非流式响应。"""
        choices = []
        for c in data.get("choices", []):
            msg = c.get("message", {})
            choices.append(
                Choice(
                    index=c.get("index", 0),
                    finish_reason=c.get("finish_reason"),
                    message={
                        "role": msg.get("role", "assistant"),
                        "content": msg.get("content"),
                        "reasoning_content": msg.get("reasoning_content"),
                        "tool_calls": msg.get("tool_calls"),
                    },
                )
            )

        usage_raw = data.get("usage")
        usage = None
        if usage_raw:
            usage = UsageInfo(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )

        return ChatCompletionResponse(
            id=data.get("id", ""),
            object=data.get("object", "chat.completion"),
            created=data.get("created", 0),
            model=data.get("model", model),
            choices=choices,
            usage=usage,
        )

    def _parse_chunk(
        self, data: Dict[str, Any], model: str
    ) -> Optional[ChatCompletionChunk]:
        """解析单个流式 chunk。"""
        choices_raw = data.get("choices", [])
        if not choices_raw:
            # usage-only chunk
            usage_raw = data.get("usage")
            usage = None
            if usage_raw:
                usage = UsageInfo(
                    prompt_tokens=usage_raw.get("prompt_tokens", 0),
                    completion_tokens=usage_raw.get("completion_tokens", 0),
                    total_tokens=usage_raw.get("total_tokens", 0),
                )
            return ChatCompletionChunk(
                id=data.get("id", ""),
                object=data.get("object", "chat.completion.chunk"),
                created=data.get("created", 0),
                model=data.get("model", model),
                choices=[],
                usage=usage,
            )

        stream_choices = []
        for c in choices_raw:
            delta = c.get("delta", {})
            stream_choices.append(
                StreamChoice(
                    index=c.get("index", 0),
                    finish_reason=c.get("finish_reason"),
                    delta=DeltaMessage(
                        content=delta.get("content"),
                        reasoning_content=delta.get("reasoning_content"),
                        tool_calls=delta.get("tool_calls"),
                        role=delta.get("role"),
                    ),
                )
            )

        usage_raw = data.get("usage")
        usage = None
        if usage_raw:
            usage = UsageInfo(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )

        return ChatCompletionChunk(
            id=data.get("id", ""),
            object=data.get("object", "chat.completion.chunk"),
            created=data.get("created", 0),
            model=data.get("model", model),
            choices=stream_choices,
            usage=usage,
        )