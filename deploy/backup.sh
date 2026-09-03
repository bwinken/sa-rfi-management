#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  deploy/backup.sh — 線上備份（不用停服務）：資料庫 + 附件 → 主機目錄
#
#  用法：bash deploy/backup.sh [目的目錄]        預設 /srv/sa-rfi-backups
#        bash deploy/backup.sh --dry-run          只印出會執行的指令
#
#  會依 setup.sh 產生的 docker-compose.yml / .env 自動判斷：
#    - SQLite     → 在 app image 裡跑 scripts/backup.py（online backup API，一致性快照）
#    - PostgreSQL → docker compose exec db pg_dump -Fc，附件另外由 backup.py 打包
#  資料放 volume 或主機目錄都不用管，因為是透過 compose 掛同一份 /data。
#  產出：sa_rfi_<時間>.db 或 .dump、uploads_<時間>.tar.gz；各保留最近 14 份。
#
#  cron 每日 02:00：
#    0 2 * * * cd /path/to/sa-rfi-management && bash deploy/backup.sh >> /var/log/sa-rfi-backup.log 2>&1
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DRY=0; DEST=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) DEST="$a" ;;
    esac
done
DEST="${DEST:-${BACKUP_DIR:-/srv/sa-rfi-backups}}"

[ -f docker-compose.yml ] || { echo "找不到 docker-compose.yml，請先 bash deploy/setup.sh" >&2; exit 1; }
[ -f .env ] || { echo "找不到 .env，請先 bash deploy/setup.sh" >&2; exit 1; }

env_value() {  # 讀 .env（去引號）
    local line; line="$(grep -E "^[[:space:]]*$1=" .env | tail -n1 || true)"
    line="${line#*=}"; line="${line%%$'\r'}"
    case "$line" in \"*\") line="${line#\"}"; line="${line%\"}" ;; \'*\') line="${line#\'}"; line="${line%\'}" ;; esac
    printf '%s' "$line"
}
run() { if [ "$DRY" = 1 ]; then printf '  $ %s\n' "$*"; else "$@"; fi; }

db_mode="$(grep -m1 '^# choices:' docker-compose.yml | tr ' ' '\n' | grep '^DB=' | cut -d= -f2 || true)"
[ -n "$db_mode" ] || db_mode="sqlite"
stamp="$(date +%Y%m%d_%H%M%S)"

echo "備份 → $DEST（資料庫：$db_mode，$(date '+%Y-%m-%d %H:%M:%S')）"
run mkdir -p "$DEST"

# 在 app 容器裡跑 backup.py：掛同一份 /data（compose 定義的 volume 或主機目錄）+ 目的目錄。
# --user 0：目的目錄是主機建的，容器預設的 UID 10001 通常寫不進去；產出檔會是 root 擁有。
# -T：不配置 tty，cron 裡才能跑；--no-deps：不要順便去起 db。
compose_run=(docker compose run --rm -T --no-deps --user 0 -v "$DEST:/backups" app)

if [ "$db_mode" = "postgres" ]; then
    pg_user="$(env_value POSTGRES_USER)"; pg_user="${pg_user:-sarfi}"
    pg_db="$(env_value POSTGRES_DB)";     pg_db="${pg_db:-sa_rfi}"
    out="$DEST/sa_rfi_${stamp}.dump"
    echo "1) PostgreSQL → $out"
    if [ "$DRY" = 1 ]; then
        printf '  $ docker compose exec -T db pg_dump -Fc --no-owner --no-acl -U %s %s > %s\n' "$pg_user" "$pg_db" "$out"
    else
        docker compose exec -T db pg_dump -Fc --no-owner --no-acl -U "$pg_user" "$pg_db" > "$out"
        echo "  DB -> $out（$(du -k "$out" | cut -f1) KB）"
    fi
    echo "2) 附件"
    run "${compose_run[@]}" python scripts/backup.py /backups --uploads-only
else
    echo "1) SQLite + 附件"
    run "${compose_run[@]}" python scripts/backup.py /backups
fi

[ "$DRY" = 1 ] || { echo; ls -lh "$DEST" | tail -n +2 | tail -6; }
echo "完成。還原方式見 deploy/README.md 第 7 節。"
