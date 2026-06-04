"""Web 应用入口：提供 Responses API 兼容的 HTTP 服务。

使用 Starlette 框架构建异步 HTTP 代理服务。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from src.config import settings
from src.core.reasoning_recovery import ReasoningRecovery
from src.core.upstream import UpstreamClient
from src.models.chat import ChatCompletionChunk
from src.models.responses import ResponsesRequest, ResponsesResponse
from src.translator import InputTranslator, OutputTranslator, SseTranslator
from src.utils.logger import (
    log_info,
    log_ok,
    log_error,
    log_request,
    log_response,
    log_token,
    log_debug,
)


# ── 全局实例 ──────────────────────────────────────────────────────────────────

input_translator = InputTranslator()
output_translator = OutputTranslator()
upstream = UpstreamClient()


# ── 路由处理 ──────────────────────────────────────────────────────────────────


async def health_check(request: Request) -> JSONResponse:
    """健康检查端点。"""
    return JSONResponse({
        "service": "codex-switch-anymodel",
        "default_model": settings.model,
        "status": "ok",
        "port": settings.port,
    })


async def handle_responses(request: Request) -> Response:
    """处理 Responses API 请求。

    这是核心端点，接受 Codex CLI 发送的 v1/responses 请求，
    翻译为 Chat Completions 请求，发送给上游 API，再将结果
    翻译回 Responses API 格式。
    """
    try:
        raw_body = await request.json()
    except json.JSONDecodeError as e:
        log_error(f"invalid JSON: {e}")
        return JSONResponse(
            {"error": {"message": f"Invalid JSON: {e}"}},
            status_code=400,
        )

    # 解析请求
    try:
        responses_req = ResponsesRequest(**raw_body)
    except Exception as e:
        log_error(f"parse request error: {e}")
        return JSONResponse(
            {"error": {"message": f"Invalid request: {e}"}},
            status_code=400,
        )

    # 翻译请求
    chat_request = input_translator.translate(responses_req)

    # 补回 reasoning
    ReasoningRecovery.recover(chat_request.messages)

    # 流式或非流式
    is_stream = responses_req.stream if responses_req.stream is not None else True

    if is_stream:
        return await _handle_stream(chat_request, responses_req)
    else:
        return await _handle_non_stream(chat_request, responses_req)


async def _handle_stream(
    chat_request: Any, responses_req: ResponsesRequest
) -> StreamingResponse:
    """处理流式请求。"""
    sse_translator = SseTranslator()

    async def event_stream():
        try:
            async for chunk in upstream.chat_completions_stream(chat_request):
                events = sse_translator.translate_chunk(chunk)
                for event in events:
                    yield _format_sse(event["event"], event["data"])

            # 完成事件
            model = chat_request.model
            done_events = sse_translator.translate_done(model)
            for event in done_events:
                yield _format_sse(event["event"], event["data"])

            # 记忆 reasoning_content
            ReasoningRecovery.remember([
                m for m in chat_request.messages if m.role == "assistant"
            ])

        except Exception as e:
            log_error(f"stream error: {e}")
            error_events = sse_translator.translate_error(str(e))
            for event in error_events:
                yield _format_sse(event["event"], event["data"])

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_non_stream(
    chat_request: Any, responses_req: ResponsesRequest
) -> JSONResponse:
    """处理非流式请求。"""
    try:
        completion = await upstream.chat_completions(chat_request)
    except Exception as e:
        log_error(f"upstream error: {e}")
        status_code = 502
        error_msg = str(e)
        return JSONResponse(
            {
                "error": {
                    "type": "upstream_error",
                    "code": "upstream_error",
                    "message": error_msg,
                }
            },
            status_code=status_code,
        )

    # 记忆 reasoning（需要包装为列表，因为 remember 接收 List[ChatMessage]）
    if completion.choices:
        ReasoningRecovery.remember([completion.choices[0].message])

    # 翻译响应
    resp = output_translator.translate(completion, chat_request.model)

    # 记录 token
    if completion.usage:
        log_token(
            prompt=completion.usage.prompt_tokens,
            completion=completion.usage.completion_tokens,
            total=completion.usage.total_tokens,
        )

    log_response(f"non-stream response: {len(resp.output)} items")

    return JSONResponse(resp.model_dump(exclude_none=True))


async def catch_all(request: Request) -> JSONResponse:
    """处理未匹配的路由。"""
    return JSONResponse(
        {"error": {"message": f"Not found: {request.url.path}"}},
        status_code=404,
    )


# ── SSE 格式化 ───────────────────────────────────────────────────────────────


def _format_sse(event: str, data: Dict[str, Any]) -> bytes:
    """将事件格式化为 SSE 格式。"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    return msg.encode("utf-8")


# ── 工具函数 ─────────────────────────────────────────────────────────────────


def _extract_user_input(raw_body: dict) -> str:
    """从 Responses API 原始请求体中提取用户输入文本。"""
    raw_input = raw_body.get("input")
    if raw_input is None:
        return ""

    if isinstance(raw_input, str):
        # 跳过系统级别的消息（如权限指令）
        if raw_input.strip().startswith("<"):
            return ""
        return raw_input.strip()

    if isinstance(raw_input, list):
        texts = []
        for item in raw_input:
            if isinstance(item, dict):
                # 只提取 role="user" 的消息
                role = item.get("role", "")
                if role != "user":
                    continue
                content = item.get("content")
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") in ("input_text", "output_text", "text"):
                                texts.append(part.get("text", ""))
        return "".join(texts).strip()

    return ""


# ── 应用创建 ──────────────────────────────────────────────────────────────────


async def on_startup() -> None:
    """应用启动时的钩子。"""
    log_ok("codex-switch-anymodel starting...")
    log_info(f"listen: http://{settings.host}:{settings.port}/v1/responses")
    log_info(f"model: {settings.model}")
    log_info(f"upstream: {settings.base_url}")
    if not settings.api_key:
        log_info("api_key: not set (some providers may not require it)")


async def on_shutdown() -> None:
    """应用关闭时的钩子。"""
    await upstream.close()
    log_ok("codex-switch-anymodel stopped")


def create_app() -> Starlette:
    """创建 Starlette 应用实例。"""
    routes = [
        Route("/", endpoint=health_check),
        Route("/health", endpoint=health_check),
        Route("/v1", endpoint=health_check),
        Route("/v1/responses", endpoint=handle_responses, methods=["POST"]),
        Route("/responses", endpoint=handle_responses, methods=["POST"]),
    ]

    app = Starlette(
        debug=False,
        routes=routes,
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )

    return app