# ── build stage：只用來裝相依套件 ─────────────────────────────
FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build
# 先只複製依賴描述，讓這層在原始碼變動時仍能命中快取
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ── runtime stage ────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# 非 root 執行；UID 固定，方便在 k8s 用 fsGroup 對齊 volume 權限
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 所有狀態（SQLite 檔、附件）都寫在這裡，部署時掛成 volume
    DATA_DIR=/data \
    # 日誌只走 stdout/stderr，交給容器平台收集
    LOG_FILE=""

# 沒掛 volume 時仍可跑起來（資料會隨容器消失），掛了就用掛上的
RUN mkdir -p /data && chown app:app /data

USER app
EXPOSE 8003

# liveness 用 /healthz（不碰 DB）；readiness 請在編排層打 /readyz
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8003/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003", "--proxy-headers", "--forwarded-allow-ips", "*"]
