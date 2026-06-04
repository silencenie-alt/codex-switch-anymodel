"""Chat Completions API 数据模型。

各种上游模型提供商使用 Chat Completions API (v1/chat/completions) 协议。
这些模型定义了请求/响应的数据结构。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class FunctionCall(BaseModel):
    """函数调用定义。"""
    name: str = ""
    arguments: str = ""


class ToolCall(BaseModel):
    """工具调用。"""
    id: str = ""
    type: str = "function"
    function: FunctionCall = Field(default_factory=FunctionCall)
    index: Optional[int] = None


class ChatMessage(BaseModel):
    """Chat Completions 中的消息。"""
    role: str = "user"
    content: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ToolFunction(BaseModel):
    """工具定义中的函数描述。"""
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ChatTool(BaseModel):
    """Chat Completions 中的工具定义。"""
    type: str = "function"
    function: ToolFunction = Field(default_factory=ToolFunction)


class ThinkingParam(BaseModel):
    """思考/推理参数。"""
    type: str = "enabled"  # enabled / disabled


class ChatCompletionRequest(BaseModel):
    """发送到上游 API 的 Chat Completions 请求体。"""
    model: str
    messages: List[ChatMessage] = Field(default_factory=list)
    stream: bool = True
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[ChatTool]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    thinking: Optional[ThinkingParam] = None
    # 扩展字段
    extra_body: Dict[str, Any] = Field(default_factory=dict)


class DeltaMessage(BaseModel):
    """SSE 流式返回中的增量消息。"""
    content: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    role: Optional[str] = None


class Choice(BaseModel):
    """非流式响应中的选项。"""
    index: int = 0
    message: ChatMessage = Field(default_factory=ChatMessage)
    finish_reason: Optional[str] = None


class StreamChoice(BaseModel):
    """流式响应中的选项。"""
    index: int = 0
    delta: DeltaMessage = Field(default_factory=DeltaMessage)
    finish_reason: Optional[str] = None


class UsageInfo(BaseModel):
    """Token 用量信息。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """非流式 Chat Completions 响应。"""
    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: List[Choice] = Field(default_factory=list)
    usage: Optional[UsageInfo] = None


class ChatCompletionChunk(BaseModel):
    """流式 Chat Completions 响应块。"""
    id: str = ""
    object: str = "chat.completion.chunk"
    created: int = 0
    model: str = ""
    choices: List[StreamChoice] = Field(default_factory=list)
    usage: Optional[UsageInfo] = None