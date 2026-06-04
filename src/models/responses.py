"""OpenAI Responses API 数据模型。

Codex CLI 使用 Responses API (v1/responses) 协议。
这些模型定义了请求/响应的数据结构。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ── Input Items ──────────────────────────────────────────────────────────────


class ResponseInputTextContent(BaseModel):
    """输入中的文本内容块。"""
    type: str = "input_text"
    text: str


class ResponseInputImageContent(BaseModel):
    """输入中的图片内容块（当前会跳过）。"""
    type: str = "input_image"
    image_url: str = ""


class ResponseInputFileContent(BaseModel):
    """输入中的文件内容块（当前会跳过）。"""
    type: str = "input_file"
    file_data: str = ""
    filename: str = ""


ResponseInputContent = Union[
    ResponseInputTextContent,
    ResponseInputImageContent,
    ResponseInputFileContent,
]


class ResponseInputMessage(BaseModel):
    """role-based 输入消息。"""
    role: str = "user"  # user, assistant, system, developer
    content: Union[str, List[ResponseInputContent]]
    reasoning_content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ResponseFunctionCallInput(BaseModel):
    """function_call 类型的输入项。"""
    type: str = "function_call"
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    status: str = "completed"
    reasoning_content: Optional[str] = None


class ResponseFunctionCallOutput(BaseModel):
    """function_call_output 类型的输入项。"""
    type: str = "function_call_output"
    call_id: str = ""
    output: str = ""
    status: str = "completed"


class ResponseReasoningInput(BaseModel):
    """reasoning 类型的输入项。"""
    type: str = "reasoning"
    reasoning_content: Optional[str] = None


class ResponseMessageInput(BaseModel):
    """message 类型的输入项。"""
    type: str = "message"
    content: Union[str, List[ResponseInputContent], None] = None
    role: str = "user"


ResponseInputItem = Union[
    ResponseInputMessage,
    ResponseFunctionCallInput,
    ResponseFunctionCallOutput,
    ResponseReasoningInput,
    ResponseMessageInput,
]


# ── 请求 ─────────────────────────────────────────────────────────────────────


class ToolParam(BaseModel):
    """工具定义（Responses 格式）。"""
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ResponsesRequest(BaseModel):
    """Codex CLI 发送到 Responses API 的请求体。"""
    input: Union[str, List[ResponseInputItem], None] = None
    instructions: Optional[str] = None
    model: Optional[str] = None
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    tools: Optional[List[ToolParam]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    thinking: Optional[Union[bool, Dict[str, str]]] = None
    reasoning: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, str]] = None
    # 自定义扩展字段
    instructions_model: Optional[str] = None


# ── Output Items (SSE events) ────────────────────────────────────────────────


class OutputTextPart(BaseModel):
    """输出文本内容块。"""
    type: str = "output_text"
    text: str = ""
    annotations: List[str] = Field(default_factory=list)


class ReasoningTextPart(BaseModel):
    """推理内容块。"""
    type: str = "reasoning_text"
    text: str = ""


ResponseContentPart = Union[OutputTextPart, ReasoningTextPart]


class ResponseMessageItem(BaseModel):
    """消息类型的输出项。"""
    id: str = ""
    type: str = "message"
    role: str = "assistant"
    status: str = "in_progress"
    content: List[ResponseContentPart] = Field(default_factory=list)


class ResponseReasoningItem(BaseModel):
    """推理类型的输出项。"""
    id: str = ""
    type: str = "reasoning"
    status: str = "in_progress"
    content: List[ReasoningTextPart] = Field(default_factory=list)


class ResponseFunctionCallItem(BaseModel):
    """函数调用类型的输出项。"""
    id: str = ""
    type: str = "function_call"
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    status: str = "in_progress"


ResponseOutputItem = Union[
    ResponseMessageItem,
    ResponseReasoningItem,
    ResponseFunctionCallItem,
]


class ResponseUsage(BaseModel):
    """Token 用量统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ResponsesResponse(BaseModel):
    """完整的 Responses API 响应（非流式模式）。"""
    id: str = ""
    object: str = "response"
    status: str = "completed"
    model: str = ""
    output: List[ResponseOutputItem] = Field(default_factory=list)
    usage: Optional[ResponseUsage] = None