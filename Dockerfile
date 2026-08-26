# ── build stage：只用來裝相依套件 ─────────────────────────────
FROM python:3.11-slim-bookworm AS builder

# 內網環境常見需求：走 proxy、用私有 PyPI 鏡像、信任公司自簽 CA。
# 需要時用 --build-arg 帶進來；都不給就是一般公開網路的行為。
#   docker build \
#     --build-arg HTTPS_PROXY=http://proxy.corp:3128 \
#     --build-arg PIP_INDEX_URL=https://nexus.corp/repository/pypi/simple \
#     --build-arg PIP_TRUSTED_HOST=nexus.corp .
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ARG PIP_INDEX_URL="https://pypi.org/simple"
ARG PIP_TRUSTED_HOST=""

ENV HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY} \
    http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=${PIP_INDEX_URL}

# 公司自簽 CA：把 .crt 丟進 certs/ 即可；目錄空的話這步什麼也不做
COPY certs/ /usr/local/share/ca-certificates/
RUN if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then \
        update-ca-certificates; \
    fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    UV_NATIVE_TLS=1

# --trusted-host / --allow-insecure-host 只在有給值時才加上去
RUN pip install --no-cache-dir \
        ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
        uv==0.9.28

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# 路徑必須與 runtime 階段一致：venv 裡的執行檔 shebang 會寫死絕對路徑，
# 若在 /build 建好再複製到 /app，會變成 "exec .../uvicorn: no such file or directory"
WORKDIR /app
# 先只複製依賴描述，讓這層在原始碼變動時仍能命中快取
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
        ${PIP_TRUSTED_HOST:+--allow-insecure-host ${PIP_TRUSTED_HOST}}

# ── runtime stage ────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# 非 root 執行；UID 固定，方便在 k8s 用 fsGroup 對齊 volume 權限
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

# 執行階段也要信任同一組 CA —— App 要用 HTTPS 連 Auth Center
COPY certs/ /usr/local/share/ca-certificates/
RUN if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then \
        update-ca-certificates; \
    fi

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 讓 httpx / PyJWT 連 Auth Center 時用系統信任清單（含上面裝進去的公司 CA）
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
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
