#!/usr/bin/env python3
"""線上備份：SQLite 資料庫 + 附件目錄。

服務運行中也可安全執行（使用 SQLite online backup API，不需停機）。
讀取與主程式相同的 .env 設定（SQLITE_PATH / UPLOAD_DIR）。

用法：
    python scripts/backup.py [備份目的資料夾，預設 ./backups]

建議搭配 cron，例如每日 02:00：
    0 2 * * * cd /path/to/sa-rfi-management && .venv/bin/python scripts/backup.py /data/backups
"""

import os
import sqlite3
import sys
import tarfile
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SQLITE_PATH = Path(os.getenv("SQLITE_PATH", "./sa_rfi.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
KEEP = int(os.getenv("BACKUP_KEEP", "14"))  # 保留最近幾份


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "./backups")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) SQLite online backup（一致性快照，不影響進行中的交易）
    db_out = dest / f"sa_rfi_{stamp}.db"
    src = sqlite3.connect(str(SQLITE_PATH))
    dst = sqlite3.connect(str(db_out))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"DB  -> {db_out}")

    # 2) 附件目錄打包
    if UPLOAD_DIR.exists():
        tar_out = dest / f"uploads_{stamp}.tar.gz"
        with tarfile.open(tar_out, "w:gz") as tar:
            tar.add(UPLOAD_DIR, arcname="uploads")
        print(f"檔案 -> {tar_out}")

    # 3) 清理舊備份（各自保留最近 KEEP 份）
    for pattern in ("sa_rfi_*.db", "uploads_*.tar.gz"):
        old = sorted(dest.glob(pattern))[:-KEEP]
        for p in old:
            p.unlink()
            print(f"清理 -> {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
