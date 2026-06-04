"""codex-switch-anymodel 入口点。

启动 Uvicorn 服务器，提供 Responses API 兼容的 HTTP 代理服务。
"""

from __future__ import annotations

import uvicorn

from src.config import settings
from src.app import create_app


app = create_app()


def main() -> None:
    """启动代理服务。"""
    # 禁用 Uvicorn 默认日志，使用我们自己的 loguru 配置
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="warning",  # 只显示 uvicorn 的 warning 以上
        access_log=False,      # 禁用 uvicorn 的访问日志
    )


if __name__ == "__main__":
    main()