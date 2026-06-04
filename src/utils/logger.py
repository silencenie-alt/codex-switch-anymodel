"""优雅的日志系统。

基于 loguru 实现，支持：
- 彩色控制台输出
- 自动文件日志轮转
- 按请求/响应归类，方便追踪
- 请求体/响应体预览打印
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import settings


# ── 自定义日志级别 ────────────────────────────────────────────────────────────

# 自定义日志级别名称和格式:
# [INFO]  [ OK ]  [WARN]  [ERR ]  [REQUEST]  [RESPONSE]  [SKIP]  [TOKS]

REQUEST_LEVEL = 21   # 请求日志  -> [REQUEST]
RESPONSE_LEVEL = 22  # 响应日志   -> [RESPONSE]
OK_LEVEL = 23        # 成功日志    -> [ OK ]
TOKEN_LEVEL = 15     # Token 用量  -> [TOKS]
SKIP_LEVEL = 18      # 跳过记录    -> [SKIP]

logger.level("REQUEST",  no=REQUEST_LEVEL,  color="<light-yellow>")
logger.level("RESPONSE", no=RESPONSE_LEVEL, color="<light-cyan>")
logger.level(" OK ", no=OK_LEVEL,       color="<light-green>")
logger.level("TOKS", no=TOKEN_LEVEL,    color="<dim><white>")
logger.level("SKIP", no=SKIP_LEVEL,     color="<dim><white>")


# ── 移除默认处理器 ────────────────────────────────────────────────────────────

logger.remove()


# ── 级别名称映射 ──────────────────────────────────────────────────────────────

_LEVEL_LABELS = {
    REQUEST_LEVEL:  "REQUEST",
    RESPONSE_LEVEL: "RESPONSE",
    OK_LEVEL:       " OK ",
    TOKEN_LEVEL:    "TOKS",
    SKIP_LEVEL:     "SKIP",
    # 标准级别
    10: "DEBUG",
    20: "INFO",
    30: "WARN",
    40: "ERR",
}

_LEVEL_COLORS = {
    REQUEST_LEVEL:  "light-yellow",
    RESPONSE_LEVEL: "light-cyan",
    OK_LEVEL:       "light-green",
    TOKEN_LEVEL:    "dim",
    SKIP_LEVEL:     "dim",
    10: "dim",
    20: "cyan",
    30: "yellow",
    40: "red",
}

# 统一对齐宽度：所有标签括号内内容按此宽度左对齐填充
# 最长的是 "RESPONSE"(8字符)
_LABEL_INNER_WIDTH = 8


def _get_level_label(level_no: int) -> str:
    """获取格式化的级别标签，所有标签统一宽度对齐。"""
    name = _LEVEL_LABELS.get(level_no, "")
    return f"[{name:<{_LABEL_INNER_WIDTH}}]"


# ── 控制台处理器 ──────────────────────────────────────────────────────────────


def _escape_loguru_format(msg: str) -> str:
    """转义消息中的 `{` 和 `}`，防止 loguru 误解析为格式语法。"""
    return msg.replace("{", "{{").replace("}", "}}")


def _console_format(record) -> str:
    """自定义控制台格式器。

    输出格式: `[LEVEL] msg`
    无时间戳。
    """
    level_no = record["level"].no
    label = _get_level_label(level_no)
    level_color = _LEVEL_COLORS.get(level_no, "")

    msg = record["message"].replace("\n", " ").replace("\r", "")
    msg = _escape_loguru_format(msg)

    if level_color:
        return f"<{level_color}>{label}</{level_color}> {msg}\n"
    return f"{label} {msg}\n"


logger.add(
    sink=sys.stdout,
    level=settings.log_level.upper(),
    format=_console_format,
    colorize=True,
    backtrace=False,
    diagnose=False,
)


# ── 文件处理器 ────────────────────────────────────────────────────────────────


def _file_format(record) -> str:
    """自定义文件格式器。

    输出格式: `[LEVEL] YYYY-MM-DD HH:MM:SS.SSS | msg`
    """
    level_no = record["level"].no
    label = _get_level_label(level_no)
    time_str = record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
    msg = record["message"].replace("\n", " | ").replace("\r", "")
    msg = _escape_loguru_format(msg)
    return f"{label} {time_str} | {msg}\n"


def _ensure_log_dir(path: str) -> Path:
    """确保日志目录存在。"""
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


logger.add(
    sink=str(_ensure_log_dir(settings.log_file)),
    level="DEBUG",
    format=_file_format,
    rotation="100 MB",
    retention=settings.log_retention,
    compression="gz",
    backtrace=True,
    diagnose=True,
    enqueue=True,
)


# ── JSON 截断格式化 ──────────────────────────────────────────────────────────


def _format_body_preview(body: Any, max_len: int = 300) -> str:
    """将对象格式化为 JSON 预览字符串，过长时截断。"""
    try:
        if isinstance(body, str):
            raw = body
        else:
            raw = _json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(body)

    if len(raw) > max_len:
        raw = raw[:max_len] + "..."
    return raw


# ── 公共 API ──────────────────────────────────────────────────────────────────


def log_request(msg: str, **kwargs) -> None:
    """记录一个请求事件。

    示例: `[REQUEST] thinking:on msgs:3 stream:true | 你是谁`
    """
    extra = _fmt_extra(kwargs)
    logger.log(REQUEST_LEVEL, f"{msg}{extra}")


def log_response(msg: str, **kwargs) -> None:
    """记录一个响应事件。

    示例: `[RESPONSE] text output: 997 chars` / `[RESPONSE] reasoning output: 105 chars`
    """
    extra = _fmt_extra(kwargs)
    logger.log(RESPONSE_LEVEL, f"{msg}{extra}")


def log_token(prompt: int = 0, completion: int = 0, total: int = 0) -> None:
    """记录 token 用量。

    示例: `[TOKS] in:6698 out:230 total:6928`
    """
    parts = []
    if prompt:
        parts.append(f"in:{prompt}")
    if completion:
        parts.append(f"out:{completion}")
    if total:
        parts.append(f"total:{total}")
    if parts:
        logger.log(TOKEN_LEVEL, " ".join(parts))


def log_skip(reason: str, **kwargs) -> None:
    """记录被跳过的内容。"""
    extra = _fmt_extra(kwargs)
    logger.log(SKIP_LEVEL, f"skip: {reason}{extra}")


def log_info(msg: str, **kwargs) -> None:
    """记录一条普通信息。"""
    extra = _fmt_extra(kwargs)
    logger.info(f"{msg}{extra}")


def log_ok(msg: str, **kwargs) -> None:
    """记录一条成功信息（使用 [ OK ] 级别）。"""
    extra = _fmt_extra(kwargs)
    logger.log(OK_LEVEL, f"{msg}{extra}")


def log_warn(msg: str, **kwargs) -> None:
    """记录一条警告。"""
    extra = _fmt_extra(kwargs)
    logger.warning(f"{msg}{extra}")


def log_error(msg: str, **kwargs) -> None:
    """记录一条错误。"""
    extra = _fmt_extra(kwargs)
    logger.error(f"{msg}{extra}")


def log_debug(msg: str, **kwargs) -> None:
    """记录一条调试信息。"""
    extra = _fmt_extra(kwargs)
    logger.debug(f"{msg}{extra}")


def log_rc_restored(count: int) -> None:
    """记录 reasoning_content 被恢复的数量。"""
    log_ok(f"rc restored x{count}")


def log_rc_preserved(count: int) -> None:
    """记录 reasoning_content 被保留的数量。"""
    log_info(f"rc preserved x{count}")


def log_rc_stripped(count: int) -> None:
    """记录 reasoning_content 被移除的数量。"""
    log_skip(f"rc stripped x{count}")


def log_request_body(prefix: str, body: Any, max_len: int = 500) -> None:
    """打印请求体 JSON 预览（DEBUG 级别）。"""
    preview = _format_body_preview(body, max_len=max_len)
    logger.debug(f"→ body ({prefix}): {preview}")


def log_response_body(prefix: str, body: Any, max_len: int = 500) -> None:
    """打印响应体 JSON 预览（DEBUG 级别）。"""
    preview = _format_body_preview(body, max_len=max_len)
    logger.debug(f"← body ({prefix}): {preview}")


def _fmt_extra(kwargs: dict) -> str:
    """将键值对格式化为日志后缀。"""
    if not kwargs:
        return ""
    parts = []
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    return " [" + " ".join(parts) + "]"