#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  deploy/setup.sh — 一支腳本問完所有部署設定，產生 .env 與 docker-compose.yml
#
#  用法：
#    bash deploy/setup.sh                 # 全部（先 .env 再 docker-compose.yml）
#    bash deploy/setup.sh --env-only      # 只重做 .env（改網址 / secret / proxy）
#    bash deploy/setup.sh --compose-only  # 只重做 docker-compose.yml（改埠 / 資料位置 / DB）
#
#  - 一個一個問，必填沒填會一直問到填為止；直接 Enter 採用 [方括號] 裡的預設
#  - 重跑時，上次的值會當預設（.env 讀既有值；compose 讀檔頭的 choices）
#  - secret 不回顯；.env 權限 600
#  - 偵測到既有 docker-compose.yml 才警示：本腳本產生的 → 沿用選擇；不是的 → 先備份
#  - 最後用 docker compose config 驗證，並列出 build 會帶入的 proxy / PyPI / trusted host
#
#  產生後：docker compose build && docker compose up -d
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"
MARK="# choices:"

DO_ENV=1; DO_COMPOSE=1
case "${1:-}" in
    "") ;;
    --env-only)     DO_COMPOSE=0 ;;
    --compose-only) DO_ENV=0 ;;
    -h|--help)      sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "未知參數：$1（可用 --env-only / --compose-only）" >&2; exit 2 ;;
esac

# ═════════════════════════════════════════════════════════════════
#  共用：輸出樣式、讀值、互動
# ═════════════════════════════════════════════════════════════════
if [ -t 1 ]; then
    B=$'\033[1m'; DIM=$'\033[2m'; CY=$'\033[36m'; YE=$'\033[33m'; RD=$'\033[31m'; GN=$'\033[32m'; NC=$'\033[0m'
else
    B=""; DIM=""; CY=""; YE=""; RD=""; GN=""; NC=""
fi
section() { printf '\n%s%s── %s ──%s\n' "$B" "$CY" "$1" "$NC"; }
part()    { printf '\n%s%s════ %s ════%s\n' "$B" "$CY" "$1" "$NC"; }
note()    { printf '%s   %s%s\n' "$DIM" "$1" "$NC"; }
warn()    { printf '%s%s   ⚠ %s%s\n' "$B" "$YE" "$1" "$NC"; }
err()     { printf '%s%s   ✗ %s%s\n' "$B" "$RD" "$1" "$NC"; }
ok()      { printf '%s   ✓ %s%s\n' "$GN" "$1" "$NC"; }

# env_value FILE VAR → 印出 .env 裡的值（去外層引號、還原跳脫），沒有就印空
env_value() {
    [ -f "$1" ] || return 0
    local line
    line="$(grep -E "^[[:space:]]*$2=" "$1" | tail -n1 || true)"
    [ -n "$line" ] || return 0
    line="${line#*=}"; line="${line%%$'\r'}"
    case "$line" in
        \"*\") line="${line#\"}"; line="${line%\"}"
               line="${line//\\\$/\$}"; line="${line//\\\"/\"}"; line="${line//\\\\/\\}" ;;
        \'*\') line="${line#\'}"; line="${line%\'}" ;;
    esac
    printf '%s' "$line"
}
# choice_value VAR → 印出既有 docker-compose.yml 檔頭 choices 裡的值
choice_value() {
    [ -f "$COMPOSE_FILE" ] || return 0
    local line
    line="$(grep -m1 "^$MARK" "$COMPOSE_FILE" || true)"
    [ -n "$line" ] || return 0
    printf '%s\n' "$line" | tr ' ' '\n' | grep -E "^$1=" | head -n1 | cut -d= -f2- || true
}
# prev_value VAR → 上次的值：.env 有就用 .env，否則看 compose 檔頭
prev_value() {
    local v; v="$(env_value "$ENV_FILE" "$1")"
    [ -n "$v" ] || v="$(choice_value "$1")"
    printf '%s' "$v"
}

# 結果暫存（bash 3.2 相容）
VAR_NAMES=(); VAR_VALUES=()
set_var() {
    local i
    for i in "${!VAR_NAMES[@]}"; do
        if [ "${VAR_NAMES[$i]}" = "$1" ]; then VAR_VALUES[$i]="$2"; return; fi
    done
    VAR_NAMES+=("$1"); VAR_VALUES+=("$2")
}
get_var() {
    local i
    for i in "${!VAR_NAMES[@]}"; do
        if [ "${VAR_NAMES[$i]}" = "$1" ]; then printf '%s' "${VAR_VALUES[$i]}"; return; fi
    done
}

# ask VAR "說明" "預設" [mode]   mode：plain | required | url | url_required | bool | secret | int | path
ask() {
    local var="$1" desc="$2" default="$3" mode="${4:-plain}"
    local prev; prev="$(prev_value "$var")"
    [ -n "$prev" ] && default="$prev"
    local value
    while true; do
        printf '\n%s%s%s\n' "$B" "$var" "$NC"
        note "$desc"
        if [ "$mode" = "secret" ]; then
            if [ -n "$default" ]; then printf '   輸入（不回顯；直接 Enter 保留現有值）: '
            else                        printf '   輸入（不回顯）: '; fi
            IFS= read -r -s value; printf '\n'
        else
            if [ -n "$default" ]; then printf '   [%s]: ' "$default"; else printf '   : '; fi
            IFS= read -r value
        fi
        [ -n "$value" ] || value="$default"
        value="${value#"${value%%[![:space:]]*}"}"; value="${value%"${value##*[![:space:]]}"}"
        case "$mode" in
            required|url_required|secret|path)
                if [ -z "$value" ]; then err "這一項是必填。"; continue; fi ;;
        esac
        case "$mode" in
            url|url_required)
                if [ -n "$value" ]; then
                    case "$value" in
                        http://*|https://*) ;;
                        *) err "請填完整網址，要以 http:// 或 https:// 開頭。"; continue ;;
                    esac
                    value="${value%/}"
                fi ;;
            bool)
                case "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" in
                    true|t|yes|y|1)    value="true" ;;
                    false|f|no|n|0|"") value="false" ;;
                    *) err "請填 true 或 false。"; continue ;;
                esac ;;
            int)  case "$value" in ''|*[!0-9]*) err "請填整數。"; continue ;; esac ;;
            path) case "$value" in /*) ;; *) err "請填絕對路徑（以 / 開頭）。"; continue ;; esac; value="${value%/}" ;;
        esac
        break
    done
    set_var "$var" "$value"
    if [ "$mode" = "secret" ]; then ok "$var = ${value:0:3}…（已隱藏）"; else ok "$var = ${value:-（空）}"; fi
}

# yesno "問題" default(y|n) → 0 = yes
yesno() {
    local q="$1" def="${2:-n}" ans hint
    if [ "$def" = "y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    while true; do
        printf '\n%s%s%s %s ' "$B" "$q" "$NC" "$hint"
        IFS= read -r ans
        ans="$(printf '%s' "${ans:-$def}" | tr '[:upper:]' '[:lower:]')"
        case "$ans" in y|yes) return 0 ;; n|no) return 1 ;; *) err "請回答 y 或 n。" ;; esac
    done
}

# choose VAR "問題" "選項1|說明1" "選項2|說明2" ...（第一個為預設；上次的值優先）
choose() {
    local var="$1" q="$2"; shift 2
    local opts=("$@") keys=() i key desc default ans
    for i in "${!opts[@]}"; do keys+=("${opts[$i]%%|*}"); done
    default="$(prev_value "$var")"; [ -n "$default" ] || default="$(get_var "$var")"; [ -n "$default" ] || default="${keys[0]}"
    while true; do
        printf '\n%s%s%s\n' "$B" "$q" "$NC"
        for i in "${!opts[@]}"; do
            key="${opts[$i]%%|*}"; desc="${opts[$i]#*|}"
            printf '   %s) %-10s %s%s%s\n' "$((i + 1))" "$key" "$DIM" "$desc" "$NC"
        done
        printf '   選擇（編號或名稱）[%s]: ' "$default"
        IFS= read -r ans; ans="${ans:-$default}"
        case "$ans" in ''|*[!0-9]*) ;; *) if [ "$ans" -ge 1 ] && [ "$ans" -le "${#keys[@]}" ]; then ans="${keys[$((ans - 1))]}"; fi ;; esac
        for key in "${keys[@]}"; do
            if [ "$key" = "$ans" ]; then set_var "$var" "$key"; ok "$var = $key"; return 0; fi
        done
        err "請選 1–${#keys[@]} 或直接輸入名稱。"
    done
}

# .env 寫檔：值含特殊字元時加引號
quote() {
    local v="$1"
    case "$v" in
        *[!A-Za-z0-9_./:@,+=-]*)
            if [ "${v#*\'}" = "$v" ]; then printf "'%s'" "$v"
            else v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//\$/\\\$}"; printf '"%s"' "$v"; fi ;;
        *) printf '%s' "$v" ;;
    esac
}
emit() { printf '%s=%s\n' "$1" "$(quote "$(get_var "$1")")"; }
url_host() { local h="${1#*://}"; h="${h%%/*}"; h="${h%%:*}"; h="${h##*@}"; printf '%s' "$h"; }

# ═════════════════════════════════════════════════════════════════
printf '%s%s\nSA RFI 管理平台 — 部署設定%s\n' "$B" "$CY" "$NC"
[ "$DO_ENV" = 1 ]     && printf '   .env               → %s\n' "$ENV_FILE"
[ "$DO_COMPOSE" = 1 ] && printf '   docker-compose.yml → %s\n' "$COMPOSE_FILE"
note "直接按 Enter 採用 [方括號] 裡的預設值。Ctrl-C 隨時中止，不會寫任何東西。"

# ═════════════════════════════════════════════════════════════════
#  Part A：.env
# ═════════════════════════════════════════════════════════════════
USE_PG=0; USE_PROXY=0
if [ "$DO_ENV" = 1 ]; then
    part "A. 環境變數（.env）"
    if [ -f "$ENV_FILE" ]; then
        warn "已經有 .env。每一項會以現有值作為預設，直接 Enter 即保留。"
        yesno "要繼續嗎？（完成後會覆寫這個檔案）" y || { echo "已取消。"; exit 0; }
    fi

    section "A1. 必填"
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
    case "$(get_var APP_BASE_URL)" in https://*) cookie_default="true" ;; *) cookie_default="false" ;; esac
    ask COOKIE_SECURE "Cookie 只走 HTTPS。APP_BASE_URL 是 https 就一定要 true；純 http 測試才 false。" "$cookie_default" bool
    if [ "$(get_var COOKIE_SECURE)" = "false" ]; then
        case "$(get_var APP_BASE_URL)" in https://*) warn "APP_BASE_URL 是 https 但 COOKIE_SECURE=false，正式環境不建議。" ;; esac
    fi
    asset_default="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)"
    ask ASSET_VERSION "靜態資源版本（快取破壞用）。每次升級都要換；預設用目前的 commit sha。" "$asset_default" required

    section "A2. 認證進階"
    note "預設：REDIRECT_URI 自動組出、JWKS 自動從 Auth Center 取公鑰、離線公鑰放 /data/keys/public.pem。"
    if yesno "要調整這些嗎？（容器連不到 Auth Center 的離線環境才需要）" n; then
        ask REDIRECT_URI "留空 = 自動 $(get_var APP_BASE_URL)/auth/callback。" "" url
        ask JWKS_URL "驗章公鑰端點。留空 = 自動 $(get_var AUTH_CENTER_BASE_URL)/.well-known/jwks.json；容器連不到 Auth Center 就填 off，並把 public.pem 放進 volume 的 keys/。" ""
        ask PUBLIC_KEY_PATH "離線後備公鑰的『容器內』路徑。" "/data/keys/public.pem"
        if [ "$(printf '%s' "$(get_var JWKS_URL)" | tr '[:upper:]' '[:lower:]')" = "off" ]; then
            warn "JWKS 已停用：記得把 Auth Center 的 public.pem 放到 volume 的 keys/public.pem（見 deploy/README.md）。"
        fi
    else
        set_var REDIRECT_URI ""; set_var JWKS_URL ""; set_var PUBLIC_KEY_PATH "/data/keys/public.pem"
    fi

    section "A3. 應用設定"
    note "預設：附件上限 25 MB、投影片標題 Customer RFI Collection、無頁尾署名、LOG_LEVEL=INFO。"
    if yesno "要調整這些嗎？" n; then
        ask ROOT_PATH "反向代理子路徑前綴（如 /sa-rfi）。留空 = 由 APP_BASE_URL 自動推導，通常留空。" ""
        ask MAX_UPLOAD_MB "單一附件大小上限（MB）。反向代理的 body size 也要 ≥ 這個值。" "25" int
        ask DECK_TITLE "匯出投影片的標題（會自動接上週別區間）。" "Customer RFI Collection"
        ask DESIGNED_BY "頁尾署名 Designed by（留空不顯示）。" ""
        ask EXECUTED_BY "頁尾署名 Executed by（留空不顯示）。" ""
        ask LOG_LEVEL "日誌等級：DEBUG / INFO / WARNING / ERROR。" "INFO"
    else
        set_var ROOT_PATH "$(env_value "$ENV_FILE" ROOT_PATH)"
        set_var MAX_UPLOAD_MB "$(env_value "$ENV_FILE" MAX_UPLOAD_MB)"; [ -n "$(get_var MAX_UPLOAD_MB)" ] || set_var MAX_UPLOAD_MB "25"
        set_var DECK_TITLE "$(env_value "$ENV_FILE" DECK_TITLE)";       [ -n "$(get_var DECK_TITLE)" ]    || set_var DECK_TITLE "Customer RFI Collection"
        set_var DESIGNED_BY "$(env_value "$ENV_FILE" DESIGNED_BY)"
        set_var EXECUTED_BY "$(env_value "$ENV_FILE" EXECUTED_BY)"
        set_var LOG_LEVEL "$(env_value "$ENV_FILE" LOG_LEVEL)";         [ -n "$(get_var LOG_LEVEL)" ]     || set_var LOG_LEVEL "INFO"
    fi

    section "A4. 開發模式"
    ask DEV_AUTH_BYPASS "略過 Auth Center 認證、所有人都是 admin。正式環境一定要 false；只有 Auth Center 還沒接好、想先看畫面時才 true。" "false" bool
    if [ "$(get_var DEV_AUTH_BYPASS)" = "true" ]; then
        warn "DEV_AUTH_BYPASS=true：這個 .env 只能用在測試，任何人開網址都有全部權限。"
        yesno "確定要這樣產生嗎？" n || { set_var DEV_AUTH_BYPASS "false"; ok "已改回 false。"; }
    fi
    set_var DEV_USER "$(env_value "$ENV_FILE" DEV_USER)";     [ -n "$(get_var DEV_USER)" ]   || set_var DEV_USER "dev.user"
    set_var DEV_SCOPES "$(env_value "$ENV_FILE" DEV_SCOPES)"; [ -n "$(get_var DEV_SCOPES)" ] || set_var DEV_SCOPES "read,write,admin"

    section "A5. 資料庫"
    note "預設 SQLite（放在 volume 的 /data/sa_rfi.db），單副本綽綽有餘。要 HA 或多副本才需要 PostgreSQL。"
    pg_default="n"; [ "$(choice_value DB)" = "postgres" ] && pg_default="y"
    if yesno "要用 PostgreSQL 嗎？" "$pg_default"; then
        USE_PG=1
        ask POSTGRES_USER "PostgreSQL 使用者。" "sarfi" required
        ask POSTGRES_DB "資料庫名稱。" "sa_rfi" required
        ask POSTGRES_PASSWORD "PostgreSQL 密碼。" "" secret
        note "DATABASE_URL 會由 docker-compose.yml 自動組出，不需要手動填。"
    fi
    set_var DATABASE_URL ""

    section "A6. docker compose build：proxy / PyPI 鏡像 / trusted host（內網必填）"
    note "build 要下載 Python 套件。內網環境要走 proxy；用內部 PyPI 鏡像（Nexus / Artifactory）且它是自簽憑證時，"
    note "還要設 trusted host（pip 的 --trusted-host、uv 的 --allow-insecure-host 都會帶上）。這些都是 build args，不會進容器。"
    note "公司自簽 / 攔截式 proxy 的 CA 憑證（.crt，PEM 格式）放到 certs/，build 會裝進 image；有放 CA 的話 trusted host 通常就不需要。"
    ask BASE_IMAGE \
        "base image。官方是 python:3.11-slim；內網 registry 有鏡像就填完整位置（例：registry.corp/python:3.11-slim），daemon 就不用出外網拉。" \
        "python:3.11-slim" required
    if yesno "build 需要走 proxy 嗎？（內網環境請選 y）" y; then
        USE_PROXY=1
        ask HTTPS_PROXY "HTTPS proxy，例：http://proxy.corp:3128" "" url_required
        ask HTTP_PROXY "HTTP proxy，通常與上面相同。" "$(get_var HTTPS_PROXY)" url_required
        ask NO_PROXY "不走 proxy 的主機清單（逗號分隔）。預設已把 Auth Center 主機名加進去。" \
            "localhost,127.0.0.1,$(url_host "$(get_var AUTH_CENTER_BASE_URL)")"
        ask PIP_INDEX_URL "PyPI 來源。有內部鏡像就填（例：https://nexus.corp/repository/pypi-proxy/simple）；留空 = 官方 https://pypi.org/simple 經 proxy 下載。" "" url
        if [ -n "$(get_var PIP_INDEX_URL)" ]; then th_default="$(url_host "$(get_var PIP_INDEX_URL)")"
        else th_default="pypi.org,files.pythonhosted.org"; fi
        ask PIP_TRUSTED_HOST \
            "pip / uv 對這些主機略過 TLS 驗證（逗號分隔，可多個）。用鏡像 → 鏡像主機名；走官方 PyPI → pypi.org,files.pythonhosted.org（下載檔案在第二個）。內網 proxy 幾乎都會攔 TLS，沒設幾乎一定 build fail，所以必填。" \
            "$th_default" required
        if ! ls "$ROOT"/certs/*.crt >/dev/null 2>&1; then
            note "certs/ 目前沒有 .crt。若 proxy 會攔截 TLS（build 時出現 SSL / certificate verify failed），把公司 CA 放進去再 build。"
        fi
    else
        for v in HTTPS_PROXY HTTP_PROXY NO_PROXY PIP_INDEX_URL PIP_TRUSTED_HOST; do set_var "$v" ""; done
    fi

    # ── 寫 .env ──
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    {
        printf '# 由 deploy/setup.sh 產生於 %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
        printf '# 重新執行 bash deploy/setup.sh --env-only 可逐項修改；也可直接編輯後 docker compose up -d 套用。\n'
        printf '\n# ── 必填 ──\n'
        for v in APP_BASE_URL AUTH_CENTER_BASE_URL APP_ID CLIENT_SECRET COOKIE_SECURE ASSET_VERSION; do emit "$v"; done
        printf '\n# ── 認證進階（留空 = 自動）──\n'
        for v in REDIRECT_URI JWKS_URL PUBLIC_KEY_PATH; do emit "$v"; done
        printf '\n# ── 應用設定 ──\n'
        for v in ROOT_PATH DATABASE_URL MAX_UPLOAD_MB DECK_TITLE DESIGNED_BY EXECUTED_BY LOG_LEVEL; do emit "$v"; done
        printf '\n# ── 開發模式（正式環境保持 false）──\n'
        for v in DEV_AUTH_BYPASS DEV_USER DEV_SCOPES; do emit "$v"; done
        if [ "$USE_PG" = 1 ]; then
            printf '\n# ── PostgreSQL（docker-compose.yml 選 PostgreSQL 才用到）──\n'
            for v in POSTGRES_USER POSTGRES_DB POSTGRES_PASSWORD; do emit "$v"; done
        fi
        printf '\n# ── docker compose build 用（build args）；執行階段要走 proxy 由 docker-compose.yml 決定 ──\n'
        emit BASE_IMAGE
        if [ "$USE_PROXY" = 1 ]; then
            for v in HTTPS_PROXY HTTP_PROXY NO_PROXY PIP_INDEX_URL PIP_TRUSTED_HOST; do emit "$v"; done
        fi
    } > "$tmp"
    chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
    ok "已寫入 $ENV_FILE（權限 600）"
else
    [ -f "$ENV_FILE" ] || warn "找不到 $ENV_FILE；compose 會產生，但 docker compose 會因缺必填值而起不來。請跑 bash deploy/setup.sh --env-only。"
    [ -n "$(env_value "$ENV_FILE" POSTGRES_PASSWORD)" ] && USE_PG=1
    [ -n "$(env_value "$ENV_FILE" HTTPS_PROXY)" ] && USE_PROXY=1
fi

# ═════════════════════════════════════════════════════════════════
#  Part B：docker-compose.yml
# ═════════════════════════════════════════════════════════════════
BACKUP=""
if [ "$DO_COMPOSE" = 1 ]; then
    part "B. docker-compose.yml（這台主機專屬）"
    if [ -f "$COMPOSE_FILE" ]; then
        if grep -q "^$MARK" "$COMPOSE_FILE"; then
            warn "偵測到既有的 $(basename "$COMPOSE_FILE")（由本腳本產生）。每一項會以上次的選擇作為預設。"
            yesno "要繼續嗎？（完成後會覆寫）" y || { echo "已取消（.env 已寫好）。"; exit 0; }
        else
            warn "偵測到既有的 $(basename "$COMPOSE_FILE")，而且不是本腳本產生的。"
            BACKUP="$COMPOSE_FILE.bak.$(date +%Y%m%d%H%M%S)"
            note "繼續的話會先把它備份成 $(basename "$BACKUP")，再寫入新檔。"
            yesno "要繼續嗎？" n || { echo "已取消，未動 compose 檔。"; exit 0; }
        fi
    fi
    cdir="$(dirname "$COMPOSE_FILE")"
    for other in compose.yaml compose.yml docker-compose.yaml; do
        [ -f "$cdir/$other" ] && [ "$cdir/$other" != "$COMPOSE_FILE" ] && \
            warn "同目錄還有 $other —— docker compose 會優先讀它而不是 docker-compose.yml！請移走或改名，否則產生的檔案不會生效。"
    done
    for other in docker-compose.override.yml docker-compose.override.yaml compose.override.yaml compose.override.yml; do
        [ -f "$cdir/$other" ] && warn "同目錄有 $other —— docker compose 會自動把它疊在產生的檔案上。若那是舊的設定，請先移走。"
    done

    section "B1. 對外埠"
    ask HOST_PORT "主機上要開的埠（容器內固定 8003）。" "8003" int
    choose BIND "這個埠要開給誰連？" \
        "public|0.0.0.0：任何主機都可直連（沒有反向代理時）" \
        "local|127.0.0.1：只給同一台主機上的反向代理（nginx / Traefik）連，外面走 HTTPS"
    case "$(env_value "$ENV_FILE" APP_BASE_URL)" in
        https://*) [ "$(get_var BIND)" = "public" ] && note "APP_BASE_URL 是 https，通常代表前面有反向代理；若 HTTPS 在別台機器終結，public 沒問題。" ;;
    esac

    section "B2. image 來源"
    choose IMAGE_SOURCE "image 從哪裡來？" \
        "build|在這台主機 docker compose build（用 .env 的 proxy / PyPI 鏡像 / trusted host）" \
        "registry|從公司 registry 拉現成的 image（CI 已經 build 好、推上去）"
    if [ "$(get_var IMAGE_SOURCE)" = "registry" ]; then
        ask IMAGE_REF "image 完整名稱含 tag，例：registry.corp/sa-rfi-management:1.2.0" "" required
        asset_ver="$(env_value "$ENV_FILE" ASSET_VERSION)"; tag="$(get_var IMAGE_REF)"; tag="${tag##*:}"
        [ -n "$asset_ver" ] && [ "$asset_ver" != "$tag" ] && warn ".env 的 ASSET_VERSION=$asset_ver 與 image tag=$tag 不同；建議升級時兩者一起改成同一個值。"
    else
        set_var IMAGE_REF ""
        [ "$USE_PROXY" = 1 ] || warn ".env 裡沒有 HTTPS_PROXY；內網環境 build 會下載不到套件。需要的話重跑 bash deploy/setup.sh --env-only。"
    fi

    section "B3. 資料（SQLite 檔 + 附件 + 離線公鑰）放哪"
    choose DATA_MODE "容器的 /data 要對應到？" \
        "volume|Docker 具名 volume（預設；docker 自己管，備份用 scripts/backup.py）" \
        "bind|主機目錄（例 /srv/sa-rfi/data；方便直接看檔案、用既有的檔案備份機制）"
    if [ "$(get_var DATA_MODE)" = "bind" ]; then
        ask DATA_PATH "主機上的絕對路徑。" "/srv/sa-rfi/data" path
        dp="$(get_var DATA_PATH)"; owner="10001"
        if [ -d "$dp" ]; then
            owner="$(stat -c '%u' "$dp" 2>/dev/null || stat -f '%u' "$dp" 2>/dev/null || echo '?')"
            [ "$owner" = "10001" ] || warn "$dp 已存在但擁有者是 UID $owner；容器以 UID 10001 執行，需要 chown。"
        fi
        if [ ! -d "$dp" ] || [ "$owner" != "10001" ]; then
            if yesno "現在就建立 $dp 並 chown 10001:10001 嗎？（可能需要 sudo）" y; then
                if mkdir -p "$dp" 2>/dev/null && chown -R 10001:10001 "$dp" 2>/dev/null; then ok "已建立並設定權限。"
                elif command -v sudo >/dev/null && sudo mkdir -p "$dp" && sudo chown -R 10001:10001 "$dp"; then ok "已建立並設定權限（sudo）。"
                else warn "做不到；啟動前請手動執行：sudo mkdir -p $dp && sudo chown -R 10001:10001 $dp"; fi
            else
                note "啟動前請先執行：sudo mkdir -p $dp && sudo chown -R 10001:10001 $dp"
            fi
        fi
    else
        set_var DATA_PATH ""
    fi

    section "B4. 資料庫"
    db_default="sqlite"; [ "$USE_PG" = 1 ] && db_default="postgres"
    [ -n "$(choice_value DB)" ] || set_var DB "$db_default"   # 讓 choose 的預設反映 .env 的選擇
    choose DB "資料庫？" \
        "sqlite|SQLite（預設；單副本綽綽有餘，少一個要顧的元件）" \
        "postgres|PostgreSQL（同一份 compose 多起一個 db 服務；要 HA / 多副本才需要）"
    if [ "$(get_var DB)" = "postgres" ]; then
        [ -n "$(env_value "$ENV_FILE" POSTGRES_PASSWORD)" ] || warn ".env 裡沒有 POSTGRES_PASSWORD，db 會起不來。請重跑 bash deploy/setup.sh --env-only 並在 A5 選 PostgreSQL。"
        choose PG_DATA_MODE "PostgreSQL 的資料放哪？" "volume|Docker 具名 volume（預設）" "bind|主機目錄"
        if [ "$(get_var PG_DATA_MODE)" = "bind" ]; then
            ask PG_DATA_PATH "主機上的絕對路徑。" "/srv/sa-rfi/postgres" path
            note "postgres image 會自行處理這個目錄的權限，不需要手動 chown。"
        else
            set_var PG_DATA_PATH ""
        fi
    else
        set_var PG_DATA_MODE ""; set_var PG_DATA_PATH ""
    fi

    section "B5. 執行階段 proxy"
    note "容器執行時只會對外連一個地方：Auth Center（換 token、抓 JWKS）。"
    note ".env 的 HTTPS_PROXY 預設只用在 build；只有『容器連 Auth Center 也必須經過 proxy』才需要打開。"
    choose RUNTIME_PROXY "容器連 Auth Center 要走 proxy 嗎？" \
        "no|不用（Auth Center 在同一個內網，直連）" \
        "yes|要（把 .env 的 HTTPS_PROXY / HTTP_PROXY / NO_PROXY 帶進容器）"
    if [ "$(get_var RUNTIME_PROXY)" = "yes" ]; then
        auth="$(url_host "$(env_value "$ENV_FILE" AUTH_CENTER_BASE_URL)")"; np="$(env_value "$ENV_FILE" NO_PROXY)"
        [ -n "$auth" ] && [ "${np#*"$auth"}" = "$np" ] && warn ".env 的 NO_PROXY 沒有包含 Auth Center 主機 $auth；若 Auth Center 不需經 proxy，請加進去。"
    fi

    # ── 產生 YAML ──
    bind_ip=""; [ "$(get_var BIND)" = "local" ] && bind_ip="127.0.0.1:"
    LOGGING='    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"'
    render() {
        printf '# 由 deploy/setup.sh 產生於 %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
        printf '%s HOST_PORT=%s BIND=%s IMAGE_SOURCE=%s IMAGE_REF=%s DATA_MODE=%s DATA_PATH=%s DB=%s PG_DATA_MODE=%s PG_DATA_PATH=%s RUNTIME_PROXY=%s\n' \
            "$MARK" "$(get_var HOST_PORT)" "$(get_var BIND)" "$(get_var IMAGE_SOURCE)" "$(get_var IMAGE_REF)" \
            "$(get_var DATA_MODE)" "$(get_var DATA_PATH)" "$(get_var DB)" "$(get_var PG_DATA_MODE)" "$(get_var PG_DATA_PATH)" "$(get_var RUNTIME_PROXY)"
        cat <<'EOF'
#
# 這是這台主機專屬的 compose 設定，不進 git。環境變數的值來自同目錄的 .env；
# 重新執行 bash deploy/setup.sh --compose-only 可逐項修改，也可直接手動編輯。
# App 本身 stateless：所有狀態都在容器的 /data（下方的 volume）。

services:
  app:
EOF
        if [ "$(get_var IMAGE_SOURCE)" = "registry" ]; then
            printf '    # 用 registry 的現成 image，不在這台 build；更新用 docker compose pull\n'
            printf '    image: %s\n    pull_policy: always\n' "$(get_var IMAGE_REF)"
        else
            cat <<'EOF'
    build:
      context: .
      # ⚠ 內網 build 必要：proxy、PyPI 鏡像、trusted host（值來自 .env；公司 CA 放 certs/）
      #   PIP_TRUSTED_HOST 會同時給 pip --trusted-host 與 uv --allow-insecure-host
      args:
        BASE_IMAGE: ${BASE_IMAGE:-python:3.11-slim}
        HTTP_PROXY: ${HTTP_PROXY:-}
        HTTPS_PROXY: ${HTTPS_PROXY:-}
        NO_PROXY: ${NO_PROXY:-}
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}
        PIP_TRUSTED_HOST: ${PIP_TRUSTED_HOST:-}
    image: sa-rfi-management:${ASSET_VERSION:-latest}
EOF
        fi
        printf '    restart: unless-stopped\n'
        printf '    # 對外埠：%s\n' "$([ "$(get_var BIND)" = "local" ] && echo '只給本機的反向代理連' || echo '任何主機都可直連')"
        printf '    ports:\n      - "%s%s:8003"\n' "$bind_ip" "$(get_var HOST_PORT)"
        cat <<'EOF'
    environment:
      # ── 對外位址 ──
      APP_BASE_URL: ${APP_BASE_URL:?請在 .env 設定 APP_BASE_URL}
      # 反向代理子路徑前綴（留空 = 由 APP_BASE_URL 推導）
      ROOT_PATH: ${ROOT_PATH:-}

      # ── Auth Center SSO ──
      AUTH_CENTER_BASE_URL: ${AUTH_CENTER_BASE_URL:?請在 .env 設定 AUTH_CENTER_BASE_URL}
      APP_ID: ${APP_ID:-sa_rfi_management}
      CLIENT_SECRET: ${CLIENT_SECRET:-}
      COOKIE_SECURE: ${COOKIE_SECURE:-true}
      # 留空 = 自動組出 {APP_BASE_URL}/auth/callback，須與 Auth Center 註冊值一致
      REDIRECT_URI: ${REDIRECT_URI:-}
      # 留空 = 自動用 {AUTH_CENTER_BASE_URL}/.well-known/jwks.json；填 off 停用、只用離線公鑰
      JWKS_URL: ${JWKS_URL:-}
      # 離線後備公鑰：把 Auth Center 的 public.pem 放進 /data/keys/ 即可
      PUBLIC_KEY_PATH: ${PUBLIC_KEY_PATH:-/data/keys/public.pem}

      # ── 狀態：容器內一律寫在 /data（對應下方 volumes）──
      DATA_DIR: /data
EOF
        if [ "$(get_var DB)" = "postgres" ]; then
            printf '      # 同一份 compose 裡的 PostgreSQL（帳密來自 .env 的 POSTGRES_*）\n'
            printf '      DATABASE_URL: postgresql://${POSTGRES_USER:-sarfi}:${POSTGRES_PASSWORD:?請在 .env 設定 POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-sa_rfi}\n'
        else
            printf '      # 留空 = SQLite 在 /data/sa_rfi.db\n      DATABASE_URL: ${DATABASE_URL:-}\n'
        fi
        cat <<'EOF'

      # ── 其他 ──
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      MAX_UPLOAD_MB: ${MAX_UPLOAD_MB:-25}
      DECK_TITLE: ${DECK_TITLE:-Customer RFI Collection}
      DESIGNED_BY: ${DESIGNED_BY:-}
      EXECUTED_BY: ${EXECUTED_BY:-}
      # 多副本時設成 image tag / commit sha，讓各副本的靜態資源版本一致
      ASSET_VERSION: ${ASSET_VERSION:-}

      # ⚠️ 正式環境務必維持 false；只有 Auth Center 還沒接好、先看畫面時才 true
      DEV_AUTH_BYPASS: ${DEV_AUTH_BYPASS:-false}
      DEV_USER: ${DEV_USER:-dev.user}
      DEV_SCOPES: ${DEV_SCOPES:-read,write,admin}
EOF
        if [ "$(get_var RUNTIME_PROXY)" = "yes" ]; then
            cat <<'EOF'

      # ── 容器連 Auth Center 走 proxy（值來自 .env；Auth Center 主機請放在 NO_PROXY）──
      HTTPS_PROXY: ${HTTPS_PROXY:-}
      HTTP_PROXY: ${HTTP_PROXY:-}
      NO_PROXY: ${NO_PROXY:-localhost,127.0.0.1}
      https_proxy: ${HTTPS_PROXY:-}
      http_proxy: ${HTTP_PROXY:-}
      no_proxy: ${NO_PROXY:-localhost,127.0.0.1}
EOF
        fi
        if [ "$(get_var DATA_MODE)" = "bind" ]; then
            printf '    # 狀態放主機目錄（容器以 UID 10001 執行，目錄需 chown 10001:10001）\n'
            printf '    volumes:\n      - %s:/data\n' "$(get_var DATA_PATH)"
        else
            printf '    # 狀態放具名 volume；備份見 deploy/README.md\n    volumes:\n      - sa-rfi-data:/data\n'
        fi
        [ "$(get_var DB)" = "postgres" ] && printf '    depends_on:\n      db:\n        condition: service_healthy\n'
        cat <<'EOF'
    healthcheck:
      test: ["CMD", "python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8003/readyz', timeout=4).status == 200 else 1)"]
      interval: 30s
      timeout: 5s
      start_period: 15s
      retries: 3
    # 容器日誌輪替：單檔 10MB、保留 5 個（json-file 預設不輪替，長期跑會吃滿磁碟）
EOF
        printf '%s\n' "$LOGGING"
        if [ "$(get_var DB)" = "postgres" ]; then
            cat <<'EOF'

  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-sa_rfi}
      POSTGRES_USER: ${POSTGRES_USER:-sarfi}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?請在 .env 設定 POSTGRES_PASSWORD}
EOF
            if [ "$(get_var PG_DATA_MODE)" = "bind" ]; then printf '    volumes:\n      - %s:/var/lib/postgresql/data\n' "$(get_var PG_DATA_PATH)"
            else printf '    volumes:\n      - sa-rfi-db:/var/lib/postgresql/data\n'; fi
            cat <<'EOF'
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-sarfi} -d ${POSTGRES_DB:-sa_rfi}"]
      interval: 10s
      timeout: 5s
      retries: 5
EOF
            printf '%s\n' "$LOGGING"
        fi
        need_vol=0
        [ "$(get_var DATA_MODE)" = "volume" ] && need_vol=1
        [ "$(get_var DB)" = "postgres" ] && [ "$(get_var PG_DATA_MODE)" = "volume" ] && need_vol=1
        if [ "$need_vol" = 1 ]; then
            printf '\nvolumes:\n'
            [ "$(get_var DATA_MODE)" = "volume" ] && printf '  # SQLite 檔、附件、離線公鑰\n  sa-rfi-data:\n'
            [ "$(get_var DB)" = "postgres" ] && [ "$(get_var PG_DATA_MODE)" = "volume" ] && printf '  sa-rfi-db:\n'
        fi
        return 0
    }
    tmp="$(mktemp "${COMPOSE_FILE}.XXXXXX")"
    render > "$tmp"
    if [ -n "$BACKUP" ]; then cp -p "$COMPOSE_FILE" "$BACKUP"; ok "原檔已備份成 $(basename "$BACKUP")"; fi
    mv "$tmp" "$COMPOSE_FILE"
    ok "已寫入 $COMPOSE_FILE"
fi

# ═════════════════════════════════════════════════════════════════
#  總結、驗證、下一步
# ═════════════════════════════════════════════════════════════════
section "完成"
if [ "$DO_ENV" = 1 ]; then
    printf '\n   %s.env%s（secret 已遮罩）\n' "$B" "$NC"
    sed -E 's/^(CLIENT_SECRET|POSTGRES_PASSWORD)=.*/\1=********/' "$ENV_FILE" | grep -v '^#' | grep -v '^$' | sed 's/^/     /'
fi
if [ "$DO_COMPOSE" = 1 ]; then
    printf '\n   %sdocker-compose.yml%s\n' "$B" "$NC"
    sed -n '2p' "$COMPOSE_FILE" | sed 's/^# choices: /     /' | tr ' ' '\n' | sed 's/^/     /' | grep -v '^     $'
fi

img_src="$(choice_value IMAGE_SOURCE)"; [ -n "$img_src" ] || img_src="build"
if [ "$img_src" = "build" ]; then
    printf '\n   %sdocker compose build 會帶入：%s\n' "$B" "$NC"
    for v in BASE_IMAGE HTTPS_PROXY HTTP_PROXY NO_PROXY PIP_INDEX_URL PIP_TRUSTED_HOST; do
        val="$(env_value "$ENV_FILE" "$v")"
        [ -n "$val" ] || { [ "$v" = "PIP_INDEX_URL" ] && val="https://pypi.org/simple（官方）"; }
        [ -n "$val" ] || { [ "$v" = "BASE_IMAGE" ] && val="python:3.11-slim"; }
        printf '     %-18s %s\n' "$v" "${val:-（未設）}"
    done
    if ls "$ROOT"/certs/*.crt >/dev/null 2>&1; then printf '     %-18s %s\n' "certs/" "$(ls "$ROOT"/certs/*.crt | xargs -n1 basename | tr '\n' ' ')"
    else printf '     %-18s （無 CA；proxy 攔 TLS 的話 build 會失敗）\n' "certs/"; fi
    [ -z "$(env_value "$ENV_FILE" HTTPS_PROXY)" ] && warn "沒有 proxy：內網環境 build 會卡在下載套件。"
    [ -z "$(env_value "$ENV_FILE" PIP_TRUSTED_HOST)" ] && [ -n "$(env_value "$ENV_FILE" HTTPS_PROXY)" ] && \
        warn "沒有 PIP_TRUSTED_HOST：proxy 攔 TLS 的話 build 會 certificate verify failed（bash deploy/setup.sh --env-only 的 A6 補上）。"
    # FROM 拉 base image 是 docker daemon 做的，不吃 build args；daemon 沒 proxy、本機又沒這個 image 就會卡在第一步
    if [ -n "$(env_value "$ENV_FILE" HTTPS_PROXY)" ] && docker info >/dev/null 2>&1; then
        daemon_proxy="$(docker info --format '{{.HTTPProxy}}{{.HTTPSProxy}}' 2>/dev/null || true)"
        base_img="$(env_value "$ENV_FILE" BASE_IMAGE)"; [ -n "$base_img" ] || base_img="python:3.11-slim"
        case "$base_img" in
            */*) is_internal=1 ;;   # 有 registry 前綴 → 內網 registry，daemon 不需要 proxy
            *)   is_internal=0 ;;
        esac
        if [ "$is_internal" = 0 ] && [ -z "$daemon_proxy" ] && ! docker image inspect "$base_img" >/dev/null 2>&1; then
            warn "docker daemon 沒有設 proxy，而且本機沒有 base image $base_img —— build 第一步 FROM 就會拉不到。"
            note "兩條路：A6 把 BASE_IMAGE 指到內網 registry 的鏡像（推薦），或給 daemon 設 proxy："
            note "設定方式（systemd）：/etc/systemd/system/docker.service.d/http-proxy.conf 加上"
            note "  [Service]"
            note "  Environment=\"HTTPS_PROXY=$(env_value "$ENV_FILE" HTTPS_PROXY)\" \"HTTP_PROXY=$(env_value "$ENV_FILE" HTTP_PROXY)\" \"NO_PROXY=$(env_value "$ENV_FILE" NO_PROXY)\""
            note "然後 sudo systemctl daemon-reload && sudo systemctl restart docker，再 docker info | grep -i proxy 確認。"
        fi
    fi
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [ -f "$COMPOSE_FILE" ]; then
    printf '\n'
    if [ -f "$ENV_FILE" ]; then chk=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config -q)
    else chk=(docker compose -f "$COMPOSE_FILE" config -q); fi
    if "${chk[@]}" 2>"$COMPOSE_FILE.check"; then ok "docker compose config 驗證通過。"
    else
        err "docker compose config 驗證失敗："; sed 's/^/      /' "$COMPOSE_FILE.check"
        note "「required variable ... is missing」= .env 缺必填值 → bash deploy/setup.sh --env-only"
    fi
    rm -f "$COMPOSE_FILE.check"
fi

section "接下來"
n=1
if [ "$DO_ENV" = 1 ]; then
    cb="$(env_value "$ENV_FILE" REDIRECT_URI)"; [ -n "$cb" ] || cb="$(env_value "$ENV_FILE" APP_BASE_URL)/auth/callback"
    printf '   %s. 到 Auth Center 確認 app_id=%s 的 redirect_uri 是：%s%s%s\n' "$n" "$(env_value "$ENV_FILE" APP_ID)" "$B" "$cb" "$NC"; n=$((n + 1))
fi
if [ "$img_src" = "registry" ]; then printf '   %s. docker compose pull\n' "$n"; else printf '   %s. docker compose build\n' "$n"; fi; n=$((n + 1))
printf '   %s. docker compose up -d\n' "$n"; n=$((n + 1))
printf '   %s. docker compose logs -f app          # 看到「SA RFI 平台啟動」\n' "$n"; n=$((n + 1))
hp="$(choice_value HOST_PORT)"; [ -n "$hp" ] || hp=8003
printf '   %s. curl -s http://127.0.0.1:%s/readyz   # 應回 {"status":"ok",...}\n' "$n" "$hp"
[ "$(choice_value BIND)" = "local" ] && printf '\n   埠只綁 127.0.0.1：記得設定反向代理指到 http://127.0.0.1:%s（範例見 deploy/README.md）。\n' "$hp"
printf '\n   之後升級：git pull → bash deploy/setup.sh --env-only（改 ASSET_VERSION）→ docker compose %s → docker compose up -d\n' \
    "$([ "$img_src" = "registry" ] && echo pull || echo build)"
printf '   詳細說明：deploy/README.md\n\n'
