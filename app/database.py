"""SQLite（async SQLAlchemy）連線與 Session 管理。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # 確保 models 已被 import 後再建表
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # RFI 編號唯一（不分大小寫）補上 DB 層約束，杜絕並發建立時
    # 應用層流水號的 race condition。既有資料若已有重複，建立會失敗——
    # 只記警告、不影響服務啟動，清完重複後下次啟動自動補上。
    from loguru import logger
    from sqlalchemy import text

    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_rfis_no_lower "
                "ON rfis (lower(rfi_no))"
            ))
    except Exception as e:
        logger.warning(
            "無法建立 RFI 編號唯一索引（可能既有資料已有重複編號）：{}。"
            "唯一性暫時只由應用層檢查保障。", e,
        )
