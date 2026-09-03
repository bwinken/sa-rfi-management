#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  deploy/2_compose_setup.sh — 逐項詢問、產生這台主機的 docker-compose.yml
#
#  用法：bash deploy/2_compose_setup.sh      （先跑過 1_env_setup.sh）
#
#  問「每台主機不一樣」的事——對外埠、image 從哪來、資料放哪、要不要
#  PostgreSQL、容器連 Auth Center 是否走 proxy——然後產生一份完整、
#  自給自足的 docker-compose.yml。環境變數的值一律來自同目錄的 .env。
#
#  - 偵測到既有的 docker-compose.yml / compose.yaml / override 檔會警示
#  - 本腳本產生的檔案重跑時會讀上次的選擇當預設；不是本腳本產生的會先備份
#  - 產生後用 docker compose config 驗證（有 docker 的話）
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
OUT_FILE="${OUT_FILE:-$ROOT/docker-compose.yml}"
MARK="# choices:"

# shellcheck source=deploy/_lib.sh
. "$ROOT/deploy/_lib.sh"

# 上次的選擇記在檔頭「# choices: KEY=VALUE ...」，重跑時當預設
prev_value() {
    [ -f "$OUT_FILE" ] || return 0
    local line
    line="$(grep -m1 "^$MARK" "$OUT_FILE" || true)"
    [ -n "$line" ] || return 0
    printf '%s\n' "$line" | tr ' ' '\n' | grep -E "^$1=" | head -n1 | cut -d= -f2- || true
}

# ═════════════════════════════════════════════════════════════════
printf '%s%s\nSA RFI 管理平台 — 產生 docker-compose.yml%s\n' "$B" "$CY" "$NC"
printf '   目標檔案：%s\n' "$OUT_FILE"

if [ ! -f "$ENV_FILE" ]; then
    warn "找不到 $ENV_FILE。建議先跑 bash deploy/1_env_setup.sh，這裡會用它的值當預設與檢查。"
    yesno "仍要繼續嗎？" n || { echo "已取消。"; exit 0; }
fi

# ── 偵測既有檔案 ─────────────────────────────────────────────────
BACKUP=""
if [ -f "$OUT_FILE" ]; then
    if grep -q "^$MARK" "$OUT_FILE"; then
        warn "偵測到既有的 $(basename "$OUT_FILE")（由本腳本產生）。每一項會以上次的選擇作為預設，直接 Enter 即保留。"
        yesno "要繼續嗎？（完成後會覆寫）" y || { echo "已取消。"; exit 0; }
    else
        warn "偵測到既有的 $(basename "$OUT_FILE")，而且不是本腳本產生的。"
        BACKUP="$OUT_FILE.bak.$(date +%Y%m%d%H%M%S)"
        note "繼續的話會先把它備份成 $(basename "$BACKUP")，再寫入新檔。"
        yesno "要繼續嗎？" n || { echo "已取消，未動任何檔案。"; exit 0; }
    fi
fi
out_dir="$(dirname "$OUT_FILE")"
for other in compose.yaml compose.yml docker-compose.yaml; do
    if [ -f "$out_dir/$other" ] && [ "$out_dir/$other" != "$OUT_FILE" ]; then
        warn "同目錄還有 $other —— docker compose 會優先讀它而不是 docker-compose.yml！請移走或改名，否則產生的檔案不會生效。"
    fi
done
for other in docker-compose.override.yml docker-compose.override.yaml compose.override.yaml compose.override.yml; do
    if [ -f "$out_dir/$other" ]; then
        warn "同目錄有 $other —— docker compose 會自動把它疊在產生的檔案上。若那是舊的設定，請先移走。"
    fi
done
note "直接按 Enter 採用 [方括號] 裡的預設值。Ctrl-C 隨時中止，不會寫任何東西。"

# ── 1. 對外埠 ────────────────────────────────────────────────────
section "1/5 對外埠"
ask HOST_PORT "主機上要開的埠（容器內固定 8003）。" "8003" int
choose BIND "這個埠要開給誰連？" \
    "public|0.0.0.0：任何主機都可直連（沒有反向代理時）" \
    "local|127.0.0.1：只給同一台主機上的反向代理（nginx / Traefik）連，外面走 HTTPS"
case "$(env_value "$ENV_FILE" APP_BASE_URL)" in
    https://*) [ "$(get_var BIND)" = "public" ] && note "APP_BASE_URL 是 https，通常代表前面有反向代理；若 HTTPS 在別台機器終結，public 沒問題。" ;;
esac

# ── 2. image 來源 ────────────────────────────────────────────────
section "2/5 image 來源"
choose IMAGE_SOURCE "image 從哪裡來？" \
    "build|在這台主機 docker compose build（用 .env 裡的 proxy / PyPI 設定）" \
    "registry|從公司 registry 拉現成的 image（CI 已經 build 好、推上去）"
if [ "$(get_var IMAGE_SOURCE)" = "registry" ]; then
    ask IMAGE_REF "image 完整名稱含 tag，例：registry.corp/sa-rfi-management:1.2.0" "" required
    asset_ver="$(env_value "$ENV_FILE" ASSET_VERSION)"
    tag="$(get_var IMAGE_REF)"; tag="${tag##*:}"
    if [ -n "$asset_ver" ] && [ "$asset_ver" != "$tag" ]; then
        warn ".env 的 ASSET_VERSION=$asset_ver 與 image tag=$tag 不同；建議升級時兩者一起改成同一個值。"
    fi
else
    set_var IMAGE_REF ""
    if [ -z "$(env_value "$ENV_FILE" HTTPS_PROXY)" ]; then
        warn ".env 裡沒有 HTTPS_PROXY；內網環境 build 會下載不到套件。需要的話重跑 1_env_setup.sh 第 6 步。"
    fi
fi

# ── 3. 資料放哪 ──────────────────────────────────────────────────
section "3/5 資料（SQLite 檔 + 附件 + 離線公鑰）放哪"
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
            if mkdir -p "$dp" 2>/dev/null && chown -R 10001:10001 "$dp" 2>/dev/null; then
                ok "已建立並設定權限。"
            elif command -v sudo >/dev/null && sudo mkdir -p "$dp" && sudo chown -R 10001:10001 "$dp"; then
                ok "已建立並設定權限（sudo）。"
            else
                warn "做不到；啟動前請手動執行：sudo mkdir -p $dp && sudo chown -R 10001:10001 $dp"
            fi
        else
            note "啟動前請先執行：sudo mkdir -p $dp && sudo chown -R 10001:10001 $dp"
        fi
    fi
else
    set_var DATA_PATH ""
fi

# ── 4. 資料庫 ────────────────────────────────────────────────────
section "4/5 資料庫"
choose DB "資料庫？" \
    "sqlite|SQLite（預設；單副本綽綽有餘，少一個要顧的元件）" \
    "postgres|PostgreSQL（同一份 compose 多起一個 db 服務；要 HA / 多副本才需要）"
if [ "$(get_var DB)" = "postgres" ]; then
    if [ -z "$(env_value "$ENV_FILE" POSTGRES_PASSWORD)" ]; then
        warn ".env 裡沒有 POSTGRES_PASSWORD。請重跑 bash deploy/1_env_setup.sh 並在第 5 步選 PostgreSQL，否則 db 起不來。"
    fi
    choose PG_DATA_MODE "PostgreSQL 的資料放哪？" \
        "volume|Docker 具名 volume（預設）" \
        "bind|主機目錄"
    if [ "$(get_var PG_DATA_MODE)" = "bind" ]; then
        ask PG_DATA_PATH "主機上的絕對路徑。" "/srv/sa-rfi/postgres" path
        note "postgres image 會自行處理這個目錄的權限，不需要手動 chown。"
    else
        set_var PG_DATA_PATH ""
    fi
else
    set_var PG_DATA_MODE ""; set_var PG_DATA_PATH ""
fi

# ── 5. 執行階段 proxy ────────────────────────────────────────────
section "5/5 執行階段 proxy"
note "容器執行時只會對外連一個地方：Auth Center（換 token、抓 JWKS）。"
note ".env 的 HTTPS_PROXY 預設只用在 build；只有『容器連 Auth Center 也必須經過 proxy』才需要打開。"
choose RUNTIME_PROXY "容器連 Auth Center 要走 proxy 嗎？" \
    "no|不用（Auth Center 在同一個內網，直連）" \
    "yes|要（把 .env 的 HTTPS_PROXY / HTTP_PROXY / NO_PROXY 帶進容器）"
if [ "$(get_var RUNTIME_PROXY)" = "yes" ]; then
    auth="$(env_value "$ENV_FILE" AUTH_CENTER_BASE_URL)"; auth="${auth#*://}"; auth="${auth%%/*}"; auth="${auth%%:*}"
    np="$(env_value "$ENV_FILE" NO_PROXY)"
    if [ -n "$auth" ] && [ "${np#*"$auth"}" = "$np" ]; then
        warn ".env 的 NO_PROXY 沒有包含 Auth Center 主機 $auth；若 Auth Center 不需經 proxy，請加進去。"
    fi
fi

# ═════════════════════════════════════════════════════════════════
# 產生 YAML
bind_ip=""; [ "$(get_var BIND)" = "local" ] && bind_ip="127.0.0.1:"
LOGGING='    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"'

render() {
    printf '# 由 deploy/2_compose_setup.sh 產生於 %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    printf '%s HOST_PORT=%s BIND=%s IMAGE_SOURCE=%s IMAGE_REF=%s DATA_MODE=%s DATA_PATH=%s DB=%s PG_DATA_MODE=%s PG_DATA_PATH=%s RUNTIME_PROXY=%s\n' \
        "$MARK" "$(get_var HOST_PORT)" "$(get_var BIND)" "$(get_var IMAGE_SOURCE)" "$(get_var IMAGE_REF)" \
        "$(get_var DATA_MODE)" "$(get_var DATA_PATH)" "$(get_var DB)" "$(get_var PG_DATA_MODE)" "$(get_var PG_DATA_PATH)" "$(get_var RUNTIME_PROXY)"
    cat <<'EOF'
#
# 這是這台主機專屬的 compose 設定，不進 git。環境變數的值來自同目錄的 .env
# （bash deploy/1_env_setup.sh 產生）；重新執行 2_compose_setup.sh 可逐項修改，也可直接手動編輯。
# App 本身 stateless：所有狀態都在容器的 /data（下方的 volume）。

services:
  app:
EOF
    # ── image / build ──
    if [ "$(get_var IMAGE_SOURCE)" = "registry" ]; then
        printf '    # 用 registry 的現成 image，不在這台 build；更新用 docker compose pull\n'
        printf '    image: %s\n    pull_policy: always\n' "$(get_var IMAGE_REF)"
    else
        cat <<'EOF'
    build:
      context: .
      # 內網 build 必要的 proxy / 私有 PyPI，值來自 .env；公司 CA 放 certs/ 即可
      args:
        HTTP_PROXY: ${HTTP_PROXY:-}
        HTTPS_PROXY: ${HTTPS_PROXY:-}
        NO_PROXY: ${NO_PROXY:-}
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}
        PIP_TRUSTED_HOST: ${PIP_TRUSTED_HOST:-}
    image: sa-rfi-management:${ASSET_VERSION:-latest}
EOF
    fi
    printf '    restart: unless-stopped\n'

    # ── 埠 ──
    printf '    # 對外埠：%s\n' "$([ "$(get_var BIND)" = "local" ] && echo '只給本機的反向代理連' || echo '任何主機都可直連')"
    printf '    ports:\n      - "%s%s:8003"\n' "$bind_ip" "$(get_var HOST_PORT)"

    # ── 環境變數 ──
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

    # ── volumes ──
    if [ "$(get_var DATA_MODE)" = "bind" ]; then
        printf '    # 狀態放主機目錄（容器以 UID 10001 執行，目錄需 chown 10001:10001）\n'
        printf '    volumes:\n      - %s:/data\n' "$(get_var DATA_PATH)"
    else
        printf '    # 狀態放具名 volume；備份見 deploy/README.md 第 7 節\n'
        printf '    volumes:\n      - sa-rfi-data:/data\n'
    fi

    # ── depends_on / healthcheck / logging ──
    if [ "$(get_var DB)" = "postgres" ]; then
        printf '    depends_on:\n      db:\n        condition: service_healthy\n'
    fi
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

    # ── db 服務 ──
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
        if [ "$(get_var PG_DATA_MODE)" = "bind" ]; then
            printf '    volumes:\n      - %s:/var/lib/postgresql/data\n' "$(get_var PG_DATA_PATH)"
        else
            printf '    volumes:\n      - sa-rfi-db:/var/lib/postgresql/data\n'
        fi
        cat <<'EOF'
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-sarfi} -d ${POSTGRES_DB:-sa_rfi}"]
      interval: 10s
      timeout: 5s
      retries: 5
EOF
        printf '%s\n' "$LOGGING"
    fi

    # ── 頂層 volumes ──
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

tmp="$(mktemp "${OUT_FILE}.XXXXXX")"
render > "$tmp"
if [ -n "$BACKUP" ]; then
    cp -p "$OUT_FILE" "$BACKUP"
    ok "原檔已備份成 $(basename "$BACKUP")"
fi
mv "$tmp" "$OUT_FILE"

# ═════════════════════════════════════════════════════════════════
section "完成"
ok "已寫入 $OUT_FILE"
printf '\n'; sed 's/^/   /' "$OUT_FILE"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    printf '\n'
    if [ -f "$ENV_FILE" ]; then
        check_cmd=(docker compose --env-file "$ENV_FILE" -f "$OUT_FILE" config -q)
    else
        check_cmd=(docker compose -f "$OUT_FILE" config -q)
    fi
    if "${check_cmd[@]}" 2>"$OUT_FILE.check"; then
        ok "docker compose config 驗證通過。"
    else
        err "docker compose config 驗證失敗："
        sed 's/^/      /' "$OUT_FILE.check"
        note "「required variable ... is missing」= .env 缺必填值 → 重跑 bash deploy/1_env_setup.sh"
    fi
    rm -f "$OUT_FILE.check"
else
    note "這台沒有 docker compose，略過驗證。"
fi

section "接下來"
if [ "$(get_var IMAGE_SOURCE)" = "registry" ]; then
    printf '   1. docker compose pull\n'
else
    printf '   1. docker compose build\n'
fi
printf '   2. docker compose up -d\n'
printf '   3. docker compose logs -f app          # 看到「SA RFI 平台啟動」\n'
printf '   4. curl -s http://127.0.0.1:%s/readyz   # 應回 {"status":"ok",...}\n' "$(get_var HOST_PORT)"
if [ "$(get_var BIND)" = "local" ]; then
    printf '\n   埠只綁 127.0.0.1：記得設定反向代理指到 http://127.0.0.1:%s（範例見 deploy/README.md 第 9 節）。\n' "$(get_var HOST_PORT)"
fi
printf '\n   之後升級：git pull → 改 .env 的 ASSET_VERSION → docker compose %s → docker compose up -d\n' \
    "$([ "$(get_var IMAGE_SOURCE)" = "registry" ] && echo pull || echo build)"
printf '   詳細說明：deploy/README.md\n\n'
