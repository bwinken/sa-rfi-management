#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  deploy/1_env_setup.sh — 逐項詢問、產生 docker compose 用的 .env
#
#  用法：bash deploy/1_env_setup.sh
#
#  - 一個一個問，必填項沒填會一直問到填為止
#  - 已經有 .env 的話，現有值會當預設值（直接 Enter 就保留）
#  - secret 類輸入不回顯
#  - 寫出的 .env 權限 600
#
#  產生後：docker compose build && docker compose up -d
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

# shellcheck source=deploy/_lib.sh
. "$ROOT/deploy/_lib.sh"

# 既有 .env 的值作為預設
prev_value() { env_value "$ENV_FILE" "$1"; }
existing()   { env_value "$ENV_FILE" "$1"; }

# ── 寫檔：值含特殊字元時加引號 ───────────────────────────────────
quote() {
    local v="$1"
    case "$v" in
        *[!A-Za-z0-9_./:@,+=-]*)
            if [ "${v#*\'}" = "$v" ]; then
                printf "'%s'" "$v"            # 沒有單引號 → 單引號包起來（compose 會照字面讀）
            else
                v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//\$/\\\$}"
                printf '"%s"' "$v"
            fi ;;
        *) printf '%s' "$v" ;;
    esac
}
emit() { printf '%s=%s\n' "$1" "$(quote "$(get_var "$1")")"; }

# ═════════════════════════════════════════════════════════════════
printf '%s%s\nSA RFI 管理平台 — 產生 docker compose 用的 .env%s\n' "$B" "$CY" "$NC"
printf '   目標檔案：%s\n' "$ENV_FILE"
if [ -f "$ENV_FILE" ]; then
    warn "已經有 .env。接下來每一項會以現有值作為預設，直接 Enter 即保留。"
    yesno "要繼續嗎？（完成後會覆寫這個檔案）" y || { echo "已取消。"; exit 0; }
fi
note "直接按 Enter 採用 [方括號] 裡的預設值。Ctrl-C 隨時中止，不會寫任何東西。"

# ── 1. 必填 ──────────────────────────────────────────────────────
section "1/6 必填"
ask APP_BASE_URL \
    "本平台對外網址。OAuth callback 會是 {此值}/auth/callback，必須與 Auth Center 註冊的 redirect_uri 一致。子路徑部署請填完整含路徑。" \
    "" url_required
ask AUTH_CENTER_BASE_URL \
    "Auth Center 網址（與 Auth Center 自己設定的 AUTH_CENTER_BASE_URL 相同，否則 JWT 的 iss 對不上）。" \
    "" url_required
ask APP_ID "在 Auth Center 註冊的 app_id。" "sa_rfi_management" required
ask CLIENT_SECRET \
    "Auth Center 產生的『明文』client_secret（Admin UI 新增應用程式時顯示一次的那個）。" \
    "" secret

case "$(get_var APP_BASE_URL)" in
    https://*) cookie_default="true" ;;
    *)         cookie_default="false" ;;
esac
ask COOKIE_SECURE \
    "Cookie 只走 HTTPS。APP_BASE_URL 是 https 就一定要 true；純 http 測試才 false。" \
    "$cookie_default" bool
if [ "$(get_var COOKIE_SECURE)" = "false" ]; then
    case "$(get_var APP_BASE_URL)" in
        https://*) warn "APP_BASE_URL 是 https 但 COOKIE_SECURE=false，正式環境不建議。" ;;
    esac
fi

asset_default="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)"
ask ASSET_VERSION \
    "靜態資源版本（快取破壞用）。每次升級都要換；預設用目前的 commit sha。" \
    "$asset_default" required

# ── 2. 認證進階 ──────────────────────────────────────────────────
section "2/6 認證進階"
note "預設：REDIRECT_URI 自動組出、JWKS 自動從 Auth Center 取公鑰、離線公鑰放 /data/keys/public.pem。"
if yesno "要調整這些嗎？（容器連不到 Auth Center 的離線環境才需要）" n; then
    ask REDIRECT_URI "留空 = 自動 $(get_var APP_BASE_URL)/auth/callback。" "" url
    ask JWKS_URL \
        "驗章公鑰端點。留空 = 自動 $(get_var AUTH_CENTER_BASE_URL)/.well-known/jwks.json；容器連不到 Auth Center 就填 off，並把 public.pem 放進 volume 的 keys/。" \
        ""
    ask PUBLIC_KEY_PATH "離線後備公鑰的『容器內』路徑。" "/data/keys/public.pem"
    if [ "$(printf '%s' "$(get_var JWKS_URL)" | tr '[:upper:]' '[:lower:]')" = "off" ]; then
        warn "JWKS 已停用：記得把 Auth Center 的 public.pem 放到 volume 的 keys/public.pem（見 deploy/README.md 第 5 節）。"
    fi
else
    set_var REDIRECT_URI ""; set_var JWKS_URL ""; set_var PUBLIC_KEY_PATH "/data/keys/public.pem"
fi

# ── 3. 應用設定 ──────────────────────────────────────────────────
section "3/6 應用設定"
note "預設：附件上限 25 MB、投影片標題 Customer RFI Collection、無頁尾署名、LOG_LEVEL=INFO。"
if yesno "要調整這些嗎？" n; then
    ask ROOT_PATH "反向代理子路徑前綴（如 /sa-rfi）。留空 = 由 APP_BASE_URL 自動推導，通常留空。" ""
    ask MAX_UPLOAD_MB "單一附件大小上限（MB）。反向代理的 body size 也要 ≥ 這個值。" "25" int
    ask DECK_TITLE "匯出投影片的標題（會自動接上週別區間）。" "Customer RFI Collection"
    ask DESIGNED_BY "頁尾署名 Designed by（留空不顯示）。" ""
    ask EXECUTED_BY "頁尾署名 Executed by（留空不顯示）。" ""
    ask LOG_LEVEL "日誌等級：DEBUG / INFO / WARNING / ERROR。" "INFO"
else
    set_var ROOT_PATH "$(existing ROOT_PATH)"
    set_var MAX_UPLOAD_MB "$(existing MAX_UPLOAD_MB)"; [ -n "$(get_var MAX_UPLOAD_MB)" ] || set_var MAX_UPLOAD_MB "25"
    set_var DECK_TITLE "$(existing DECK_TITLE)";       [ -n "$(get_var DECK_TITLE)" ]    || set_var DECK_TITLE "Customer RFI Collection"
    set_var DESIGNED_BY "$(existing DESIGNED_BY)"
    set_var EXECUTED_BY "$(existing EXECUTED_BY)"
    set_var LOG_LEVEL "$(existing LOG_LEVEL)";         [ -n "$(get_var LOG_LEVEL)" ]     || set_var LOG_LEVEL "INFO"
fi

# ── 4. 開發模式 ──────────────────────────────────────────────────
section "4/6 開發模式"
ask DEV_AUTH_BYPASS \
    "略過 Auth Center 認證、所有人都是 admin。正式環境一定要 false；只有 Auth Center 還沒接好、想先看畫面時才 true。" \
    "false" bool
if [ "$(get_var DEV_AUTH_BYPASS)" = "true" ]; then
    warn "DEV_AUTH_BYPASS=true：這個 .env 只能用在測試，任何人開網址都有全部權限。"
    yesno "確定要這樣產生嗎？" n || { set_var DEV_AUTH_BYPASS "false"; ok "已改回 false。"; }
fi
set_var DEV_USER "$(existing DEV_USER)";     [ -n "$(get_var DEV_USER)" ]   || set_var DEV_USER "dev.user"
set_var DEV_SCOPES "$(existing DEV_SCOPES)"; [ -n "$(get_var DEV_SCOPES)" ] || set_var DEV_SCOPES "read,write,admin"

# ── 5. PostgreSQL ────────────────────────────────────────────────
section "5/6 資料庫"
note "預設 SQLite（放在 volume 的 /data/sa_rfi.db），單副本綽綽有餘。要 HA 或多副本才需要 PostgreSQL。"
USE_PG=0
if yesno "要用 PostgreSQL 嗎？" n; then
    USE_PG=1
    ask POSTGRES_USER "PostgreSQL 使用者。" "sarfi" required
    ask POSTGRES_DB "資料庫名稱。" "sa_rfi" required
    ask POSTGRES_PASSWORD "PostgreSQL 密碼。" "" secret
    note "DATABASE_URL 會由 2_compose_setup.sh 產生的 compose 自動組出，不需要手動填。"
fi
set_var DATABASE_URL ""

# ── 6. 內網 build ────────────────────────────────────────────────
section "6/6 內網 build（proxy / 私有 PyPI）"
note "docker compose build 要下載套件，內網環境必須走 proxy。這些是 build args，不會進容器。"
note "公司自簽 / 攔截式 proxy 的 CA 憑證（.crt，PEM 格式）另外放到 certs/，build 會裝進 image。"
USE_PROXY=0
if yesno "build 需要走 proxy 嗎？（內網環境請選 y）" y; then
    USE_PROXY=1
    ask HTTPS_PROXY "HTTPS proxy，例：http://proxy.corp:3128" "" url_required
    ask HTTP_PROXY "HTTP proxy，通常與上面相同。" "$(get_var HTTPS_PROXY)" url_required
    auth_host="$(get_var AUTH_CENTER_BASE_URL)"; auth_host="${auth_host#*://}"; auth_host="${auth_host%%/*}"; auth_host="${auth_host%%:*}"
    ask NO_PROXY "不走 proxy 的主機清單（逗號分隔）。預設已把 Auth Center 主機名加進去。" "localhost,127.0.0.1,$auth_host"
    ask PIP_INDEX_URL "PyPI 來源。有內部鏡像（Nexus / Artifactory）就填；留空 = 官方 https://pypi.org/simple 經 proxy 下載。" "" url
    ask PIP_TRUSTED_HOST "PyPI 鏡像的主機名（鏡像用自簽憑證、又沒把 CA 放進 certs/ 時才需要）。" ""
    if [ -d "$ROOT/certs" ] && ! ls "$ROOT"/certs/*.crt >/dev/null 2>&1; then
        note "certs/ 目前沒有 .crt。若 proxy 會攔截 TLS（build 時出現 SSL / certificate verify failed），把公司 CA 放進去再 build。"
    fi
fi

# ═════════════════════════════════════════════════════════════════
# 寫檔
tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
{
    printf '# 由 deploy/1_env_setup.sh 產生於 %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    printf '# 重新執行該腳本可逐項修改；也可直接編輯後 docker compose up -d 套用。\n'
    printf '\n# ── 必填 ──\n'
    for v in APP_BASE_URL AUTH_CENTER_BASE_URL APP_ID CLIENT_SECRET COOKIE_SECURE ASSET_VERSION; do emit "$v"; done
    printf '\n# ── 認證進階（留空 = 自動）──\n'
    for v in REDIRECT_URI JWKS_URL PUBLIC_KEY_PATH; do emit "$v"; done
    printf '\n# ── 應用設定 ──\n'
    for v in ROOT_PATH DATABASE_URL MAX_UPLOAD_MB DECK_TITLE DESIGNED_BY EXECUTED_BY LOG_LEVEL; do emit "$v"; done
    printf '\n# ── 開發模式（正式環境保持 false）──\n'
    for v in DEV_AUTH_BYPASS DEV_USER DEV_SCOPES; do emit "$v"; done
    if [ "$USE_PG" = 1 ]; then
        printf '\n# ── PostgreSQL（2_compose_setup.sh 第 4 步選 PostgreSQL 才用到）──\n'
        for v in POSTGRES_USER POSTGRES_DB POSTGRES_PASSWORD; do emit "$v"; done
    fi
    if [ "$USE_PROXY" = 1 ]; then
        printf '\n# ── 內網 build（build args；執行階段要走 proxy 請在 2_compose_setup.sh 第 5 步選 yes）──\n'
        for v in HTTPS_PROXY HTTP_PROXY NO_PROXY PIP_INDEX_URL PIP_TRUSTED_HOST; do emit "$v"; done
    fi
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"

# ═════════════════════════════════════════════════════════════════
section "完成"
ok "已寫入 $ENV_FILE（權限 600）"
printf '\n'
sed -E 's/^(CLIENT_SECRET|POSTGRES_PASSWORD)=.*/\1=********/' "$ENV_FILE" | sed 's/^/   /'

section "接下來"
callback="$(get_var REDIRECT_URI)"; [ -n "$callback" ] || callback="$(get_var APP_BASE_URL)/auth/callback"
printf '   1. 到 Auth Center 確認 app_id=%s 的 redirect_uri 是：%s%s%s\n' "$(get_var APP_ID)" "$B" "$callback" "$NC"
printf '   2. bash deploy/2_compose_setup.sh   # 產生 docker-compose.yml（埠、資料位置、PostgreSQL、執行階段 proxy）\n'
printf '   3. docker compose build\n'
printf '   4. docker compose up -d\n'
printf '   5. curl -s http://localhost:8003/readyz   # 應回 {"status":"ok",...}\n'
if [ "$USE_PG" = 1 ]; then
    printf '\n   你選了 PostgreSQL：第 2 步的第 4 題記得也選 PostgreSQL，db 服務才會一起起來。\n'
fi
printf '\n   詳細說明：deploy/README.md\n\n'
