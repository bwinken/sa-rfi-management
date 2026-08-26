#!/usr/bin/env python3
"""把既有的 SQLite 資料搬到 PostgreSQL。

用途：一開始用 SQLite 跑，之後要換成 PostgreSQL（例如要跑多副本）時，
把 RFI、修改紀錄、附件中繼資料整批搬過去。附件的實體檔案不受影響
（它們一直都在 UPLOAD_DIR，不在資料庫裡）。

用法：
    # 1. 先停掉服務，確保沒有人正在寫入
    docker compose down

    # 2. 執行搬移（來源預設讀 SQLITE_PATH / DATA_DIR）
    uv run python scripts/migrate_sqlite_to_postgres.py \
        --source ./data/sa_rfi.db \
        --target postgresql://sarfi:pw@localhost:5432/sa_rfi

    # 3. 把 DATABASE_URL 設成同一個 target，再啟動服務
    docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d

會保留原本的 id，所以修改紀錄與附件的關聯不會斷。
目標資料庫若已有資料會直接中止，除非加上 --force。
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text                      # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import _normalize_db_url                        # noqa: E402
from app.database import Base                                   # noqa: E402
from app.models import Attachment, Rfi, RfiRevision             # noqa: E402

# 依外鍵相依順序搬移
MODELS = [Rfi, RfiRevision, Attachment]


def _columns(model):
    return [c.name for c in model.__table__.columns]


async def migrate(source_url: str, target_url: str, force: bool, batch: int) -> int:
    src_engine = create_async_engine(source_url)
    dst_engine = create_async_engine(target_url)
    SrcSession = async_sessionmaker(src_engine, expire_on_commit=False)
    DstSession = async_sessionmaker(dst_engine, expire_on_commit=False)

    async with dst_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SrcSession() as src, DstSession() as dst:
        # 目標非空就中止，避免不小心搬兩次造成 id 撞號
        for model in MODELS:
            existing = await dst.scalar(select(func.count()).select_from(model))
            if existing and not force:
                print(
                    f"中止：目標資料庫的 {model.__tablename__} 已有 {existing} 筆資料。\n"
                    f"確定要繼續請加 --force（會保留既有資料並嘗試寫入，"
                    f"id 相同時會失敗）。",
                    file=sys.stderr,
                )
                return 1

        total = 0
        for model in MODELS:
            rows = (await src.execute(select(model))).scalars().all()
            cols = _columns(model)
            print(f"{model.__tablename__:16} {len(rows):>6} 筆", end="", flush=True)
            for i in range(0, len(rows), batch):
                chunk = rows[i:i + batch]
                await dst.execute(
                    model.__table__.insert(),
                    [{c: getattr(r, c) for c in cols} for r in chunk],
                )
            await dst.commit()
            total += len(rows)
            print("  → 完成")

        # PostgreSQL：明確指定 id 寫入後，序列不會自動前進，要手動對齊，
        # 否則下一筆新增會撞到既有 id
        if target_url.startswith("postgresql"):
            for model in MODELS:
                tbl = model.__tablename__
                await dst.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {tbl}), 1), true)"
                ))
            await dst.commit()
            print("已對齊 PostgreSQL 的 id 序列")

    await src_engine.dispose()
    await dst_engine.dispose()
    print(f"\n搬移完成，共 {total} 筆。")
    print("附件實體檔案不需搬移（一直都在 UPLOAD_DIR，不在資料庫內）。")
    return 0


def main() -> int:
    default_sqlite = os.getenv("SQLITE_PATH") or str(
        Path(os.getenv("DATA_DIR", ".")) / "sa_rfi.db"
    )
    ap = argparse.ArgumentParser(description="SQLite → PostgreSQL 資料搬移")
    ap.add_argument("--source", default=default_sqlite,
                    help=f"來源 SQLite 檔路徑（預設：{default_sqlite}）")
    ap.add_argument("--target", default=os.getenv("DATABASE_URL", ""),
                    help="目標 PostgreSQL 連線字串（預設讀 DATABASE_URL）")
    ap.add_argument("--force", action="store_true",
                    help="目標已有資料時仍繼續")
    ap.add_argument("--batch", type=int, default=500, help="每批寫入筆數")
    args = ap.parse_args()

    if not args.target:
        print("錯誤：請用 --target 或環境變數 DATABASE_URL 指定目標資料庫。",
              file=sys.stderr)
        return 2
    src_path = Path(args.source)
    if not src_path.exists():
        print(f"錯誤：找不到來源 SQLite 檔 {src_path}", file=sys.stderr)
        return 2

    source_url = f"sqlite+aiosqlite:///{src_path}"
    target_url = _normalize_db_url(args.target)
    if target_url.startswith("sqlite"):
        print("錯誤：目標看起來仍是 SQLite，請提供 PostgreSQL 連線字串。",
              file=sys.stderr)
        return 2

    print(f"來源：{src_path}")
    print(f"目標：{target_url.split('@')[-1] if '@' in target_url else target_url}\n")
    try:
        return asyncio.run(migrate(source_url, target_url, args.force, args.batch))
    except OSError as e:
        # 連不上目標資料庫是最常見的失敗，給一句話而不是一整串 traceback
        print(f"\n錯誤：連不上目標資料庫（{e}）。\n"
              f"請確認 PostgreSQL 已啟動、連線字串正確，且這台機器連得到它。",
              file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n搬移失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
