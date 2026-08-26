"""集中設定 loguru 日誌。

- 一律輸出到 stderr
- 若設定 LOG_FILE，另存到檔案（自動輪替、保留期限）
- 攔截標準 logging（含 uvicorn / fastapi）導向 loguru，統一格式
"""

import logging
import sys
from pathlib import Path

from loguru import logger

from .config import settings

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
)


class _InterceptHandler(logging.Handler):
    """把標準 logging 的訊息轉送給 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.LOG_LEVEL, format=_FORMAT, enqueue=True)

    if settings.LOG_FILE:
        Path(settings.LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.LOG_FILE,
            level=settings.LOG_LEVEL,
            format=_FORMAT,
            rotation="10 MB",
            retention="14 days",
            encoding="utf-8",
            enqueue=True,
        )

    # 接管標準 logging，讓 uvicorn / fastapi 的訊息也走 loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        lg.handlers = [_InterceptHandler()]
        lg.propagate = False
