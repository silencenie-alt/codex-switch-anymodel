"""应用配置管理。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，支持 .env 文件和环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 上游 API 配置
    api_key: str = ""
    """上游 Chat Completions API 的密钥。"""

    base_url: str = "https://api.deepseek.com"
    """上游 API 的基础 URL。"""

    model: str = "deepseek-chat"
    """默认模型名称。当客户端未指定模型时使用。"""

    model_provider: str = ""
    """模型提供商名称，用于身份声明。如果为空，则从模型名称自动推断。"""

    # 本地服务配置
    host: str = "127.0.0.1"
    """本地代理服务监听地址。"""

    port: int = 11435
    """本地代理服务监听端口。"""

    # 行为配置
    default_instructions: str = ""
    """默认的系统指令，会在每条请求前附加。"""

    log_level: str = "INFO"
    """日志级别：DEBUG, INFO, WARNING, ERROR。"""

    log_file: str = "logs/anymodel.log"
    """日志文件路径。"""

    log_retention: str = "3 days"
    """日志保留时间。"""

    request_timeout: int = 300
    """上游 API 请求超时时间（秒）。"""


settings = Settings()
"""全局单例配置实例。"""


def resolve_env_file() -> Optional[Path]:
    """查找 .env 文件位置。"""
    candidates = [
        Path(".env"),
        Path.home() / ".codex-switch" / ".env",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None