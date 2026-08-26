"""SQLite（async SQLAlchemy）連線與 Session 管理。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


def _engine_kwargs() -> dict:
    """依資料庫種類給不同的連線參數。

    SQLite：拉長 busy timeout，避免多個 worker 同時寫入時直接噴
            "database is locked"。
    PostgreSQL：開 pool_pre_ping，容器環境常見連線被中間層閒置踢掉，
            沒有 pre-ping 的話會在下一次查詢才炸。
    """
    if settings.is_sqlite:
        return {"connect_args": {"timeout": 30}}
    return {"pool_pre_ping": True, "pool_recycle": 1800}


engine = create_async_engine(
    settings.DATABASE_URL, echo=False, future=True, **_engine_kwargs()
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if settings.is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        """WAL 讓讀寫不互相阻塞；busy_timeout 讓寫入衝突時等待而非立刻失敗。"""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


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
    # 這段語法 SQLite 與 PostgreSQL 皆可用。
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
