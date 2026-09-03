# deploy/_lib.sh — 1_env_setup.sh / 2_compose_setup.sh 共用的互動函式（用 source 載入，不直接執行）
#
# 使用的腳本需自行定義：
#   prev_value VAR   → 印出上次的值（作為預設），沒有就印空
# shellcheck shell=bash

# ── 輸出樣式 ─────────────────────────────────────────────────────
if [ -t 1 ]; then
    B=$'\033[1m'; DIM=$'\033[2m'; CY=$'\033[36m'; YE=$'\033[33m'; RD=$'\033[31m'; GN=$'\033[32m'; NC=$'\033[0m'
else
    B=""; DIM=""; CY=""; YE=""; RD=""; GN=""; NC=""
fi
section() { printf '\n%s%s── %s ──%s\n' "$B" "$CY" "$1" "$NC"; }
note()    { printf '%s   %s%s\n' "$DIM" "$1" "$NC"; }
warn()    { printf '%s%s   ⚠ %s%s\n' "$B" "$YE" "$1" "$NC"; }
err()     { printf '%s%s   ✗ %s%s\n' "$B" "$RD" "$1" "$NC"; }
ok()      { printf '%s   ✓ %s%s\n' "$GN" "$1" "$NC"; }

# ── 讀 .env 裡的值（去掉外層引號並還原跳脫）─────────────────────
# env_value FILE VAR
env_value() {
    [ -f "$1" ] || return 0
    local line
    line="$(grep -E "^[[:space:]]*$2=" "$1" | tail -n1 || true)"
    [ -n "$line" ] || return 0
    line="${line#*=}"
    line="${line%%$'\r'}"
    case "$line" in
        \"*\")
            line="${line#\"}"; line="${line%\"}"
            line="${line//\\\$/\$}"; line="${line//\\\"/\"}"; line="${line//\\\\/\\}" ;;
        \'*\') line="${line#\'}"; line="${line%\'}" ;;
    esac
    printf '%s' "$line"
}

# ── 結果暫存（bash 3.2 相容，不用關聯陣列）───────────────────────
VAR_NAMES=()
VAR_VALUES=()
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

# ── 互動 ─────────────────────────────────────────────────────────
# ask VAR "說明" "預設" [mode]
#   mode：plain（可空）| required | url | url_required | bool | secret | int | path
ask() {
    local var="$1" desc="$2" default="$3" mode="${4:-plain}"
    local prev; prev="$(prev_value "$var")"
    [ -n "$prev" ] && default="$prev"

    local value
    while true; do
        printf '\n%s%s%s\n' "$B" "$var" "$NC"
        note "$desc"
        if [ "$mode" = "secret" ]; then
            if [ -n "$default" ]; then
                printf '   輸入（不回顯；直接 Enter 保留現有值）: '
            else
                printf '   輸入（不回顯）: '
            fi
            IFS= read -r -s value; printf '\n'
        else
            if [ -n "$default" ]; then
                printf '   [%s]: ' "$default"
            else
                printf '   : '
            fi
            IFS= read -r value
        fi
        [ -n "$value" ] || value="$default"
        value="${value#"${value%%[![:space:]]*}"}"   # 去頭尾空白
        value="${value%"${value##*[![:space:]]}"}"

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
            int)
                case "$value" in
                    ''|*[!0-9]*) err "請填整數。"; continue ;;
                esac ;;
            path)
                case "$value" in
                    /*) ;;
                    *) err "請填絕對路徑（以 / 開頭）。"; continue ;;
                esac
                value="${value%/}" ;;
        esac
        break
    done
    set_var "$var" "$value"
    if [ "$mode" = "secret" ]; then
        ok "$var = ${value:0:3}…（已隱藏）"
    else
        ok "$var = ${value:-（空）}"
    fi
}

# yesno "問題" default(y|n) → 回傳 0 表示 yes
yesno() {
    local q="$1" def="${2:-n}" ans hint
    if [ "$def" = "y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    while true; do
        printf '\n%s%s%s %s ' "$B" "$q" "$NC" "$hint"
        IFS= read -r ans
        ans="$(printf '%s' "${ans:-$def}" | tr '[:upper:]' '[:lower:]')"
        case "$ans" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
            *) err "請回答 y 或 n。" ;;
        esac
    done
}

# choose VAR "問題" "選項1|說明1" "選項2|說明2" ... （第一個為預設；prev_value 有值則以它為預設）
choose() {
    local var="$1" q="$2"; shift 2
    local opts=("$@") keys=() i key desc default ans
    for i in "${!opts[@]}"; do keys+=("${opts[$i]%%|*}"); done
    default="$(prev_value "$var")"; [ -n "$default" ] || default="${keys[0]}"
    while true; do
        printf '\n%s%s%s\n' "$B" "$q" "$NC"
        for i in "${!opts[@]}"; do
            key="${opts[$i]%%|*}"; desc="${opts[$i]#*|}"
            printf '   %s) %-10s %s%s%s\n' "$((i + 1))" "$key" "$DIM" "$desc" "$NC"
        done
        printf '   選擇（編號或名稱）[%s]: ' "$default"
        IFS= read -r ans
        ans="${ans:-$default}"
        case "$ans" in
            ''|*[!0-9]*) ;;
            *) if [ "$ans" -ge 1 ] && [ "$ans" -le "${#keys[@]}" ]; then ans="${keys[$((ans - 1))]}"; fi ;;
        esac
        for key in "${keys[@]}"; do
            if [ "$key" = "$ans" ]; then set_var "$var" "$key"; ok "$var = $key"; return 0; fi
        done
        err "請選 1–${#keys[@]} 或直接輸入名稱。"
    done
}
