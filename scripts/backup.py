#!/usr/bin/env python3
"""線上備份：資料庫 + 附件目錄。

服務運行中也可安全執行，兩種資料庫都支援：
  - SQLite：用 online backup API 取一致性快照，不需停機
  - PostgreSQL：呼叫 pg_dump（需要 PATH 上有 pg_dump）

讀取與主程式相同的設定（DATA_DIR / SQLITE_PATH / DATABASE_URL / UPLOAD_DIR）。

用法：
    python scripts/backup.py [備份目的資料夾，預設 ./backups] [--uploads-only]

容器部署時，讓備份工作掛上同一個 data volume 再執行，例如：
    docker run --rm -v sa-rfi-data:/data -v /srv/backups:/backups \
        -e DATA_DIR=/data sa-rfi-management:latest \
        python scripts/backup.py /backups

搭配 cron，例如每日 02:00：
    0 2 * * * cd /path/to/sa-rfi-management && .venv/bin/python scripts/backup.py /data/backups
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
SQLITE_PATH = Path(os.getenv("SQLITE_PATH") or DATA_DIR / "sa_rfi.db")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or DATA_DIR / "uploads")
DATABASE_URL = os.getenv("DATABASE_URL", "")
KEEP = int(os.getenv("BACKUP_KEEP", "14"))  # 保留最近幾份


def _is_postgres() -> bool:
    return DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+"))


def backup_sqlite(dest: Path, stamp: str) -> Path | None:
    if not SQLITE_PATH.exists():
        print(f"略過資料庫：找不到 {SQLITE_PATH}")
        return None
    out = dest / f"sa_rfi_{stamp}.db"
    src = sqlite3.connect(str(SQLITE_PATH))
    dst = sqlite3.connect(str(out))
    with dst:
        src.backup(dst)   # 一致性快照，不影響進行中的交易
    dst.close()
    src.close()
    return out


def backup_postgres(dest: Path, stamp: str) -> Path | None:
    if shutil.which("pg_dump") is None:
        print("錯誤：DATABASE_URL 指向 PostgreSQL，但 PATH 上找不到 pg_dump。", file=sys.stderr)
        return None
    # SQLAlchemy 的 +asyncpg 後綴 pg_dump 不認得，先拿掉
    url = DATABASE_URL.replace("+asyncpg", "").replace("postgres://", "postgresql://", 1)
    out = dest / f"sa_rfi_{stamp}.dump"
    # -Fc（custom format）壓縮且可用 pg_restore 選擇性還原
    result = subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(out), url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"pg_dump 失敗：{result.stderr.strip()}", file=sys.stderr)
        out.unlink(missing_ok=True)
        return None
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    dest = Path(args[0] if args else "./backups")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    failed = False

    # 1) 資料庫（--uploads-only 跳過；容器部署用 PostgreSQL 時 app image 沒有 pg_dump，
    #    由 deploy/backup.sh 改以 docker compose exec db pg_dump 備份 DB）
    if "--uploads-only" in flags:
        print("資料庫：略過（--uploads-only）")
        db_out = None
    elif _is_postgres():
        target = urlparse(DATABASE_URL.replace("+asyncpg", "")).path.lstrip("/")
        print(f"資料庫：PostgreSQL（{target}）")
        db_out = backup_postgres(dest, stamp)
    else:
        print(f"資料庫：SQLite（{SQLITE_PATH}）")
        db_out = backup_sqlite(dest, stamp)
    if db_out:
        print(f"DB   -> {db_out}（{db_out.stat().st_size / 1024:.0f} KB）")
    elif "--uploads-only" not in flags:
        failed = True

    # 2) 附件目錄打包
    if UPLOAD_DIR.exists():
        tar_out = dest / f"uploads_{stamp}.tar.gz"
        with tarfile.open(tar_out, "w:gz") as tar:
            tar.add(UPLOAD_DIR, arcname="uploads")
        print(f"附件 -> {tar_out}（{tar_out.stat().st_size / 1024:.0f} KB）")
    else:
        print(f"略過附件：找不到 {UPLOAD_DIR}")

    # 3) 清理舊備份（各自保留最近 KEEP 份）
    for pattern in ("sa_rfi_*.db", "sa_rfi_*.dump", "uploads_*.tar.gz"):
        for p in sorted(dest.glob(pattern))[:-KEEP]:
            p.unlink()
            print(f"清理 -> {p.name}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
