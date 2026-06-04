"""推理内容自动记忆与补回。

有些模型（如 DeepSeek R1）在多轮对话中如果中间有 tool_calls，
可能会丢失 reasoning_content。此模块自动记忆 reasoning_content
并在需要时将其补回到后续的 assistant 消息中。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from src.models.chat import ChatMessage
from src.utils.logger import log_info


class ReasoningRecovery:
    """推理内容记忆与恢复管理器。

    跨线程安全，使用 class-level 锁保护共享状态。
    """

    _queue: List[str] = []
    _lock = threading.Lock()

    @classmethod
    def remember(cls, messages: List[ChatMessage]) -> None:
        """记忆消息列表中的 reasoning_content。

        Args:
            messages: 包含 assistant 消息的列表，其中可能带有 reasoning_content。
        """
        with cls._lock:
            for msg in messages:
                if msg.role == "assistant" and msg.reasoning_content:
                    cls._queue.append(msg.reasoning_content)

    @classmethod
    def remember_from_non_stream(
        cls, completion_data: Dict[str, Any]
    ) -> None:
        """从非流式响应的 choices 中记忆 reasoning_content。"""
        choices = completion_data.get("choices", [])
        for choice in choices:
            msg = choice.get("message", {})
            if msg.get("reasoning_content"):
                with cls._lock:
                    cls._queue.append(msg["reasoning_content"])

    @classmethod
    def recover(cls, messages: List[ChatMessage]) -> int:
        """为 messages 中缺少 reasoning_content 的 assistant 消息补回。

        Args:
            messages: 待补回的消息列表。

        Returns:
            补回的消息数量。
        """
        with cls._lock:
            if not cls._queue:
                return 0

            recovered = 0
            for msg in messages:
                if (
                    msg.role == "assistant"
                    and msg.tool_calls
                    and not msg.reasoning_content
                ):
                    idx = min(recovered, len(cls._queue) - 1)
                    msg.reasoning_content = cls._queue[idx]
                    recovered += 1

            if recovered > 0:
                log_info(f"reasoning restored x{recovered}")

            return recovered

    @classmethod
    def clear(cls) -> None:
        """清空记忆队列。"""
        with cls._lock:
            cls._queue.clear()