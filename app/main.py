"""SA RFI 管理平台 — FastAPI 進入點。"""

import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from . import fields as fields_module
from .auth import (
    COOKIE_NAME,
    decode_token,
    exchange_code_for_token,
    optional_user,
    public_key_available,
)
from .config import prepare_storage, settings
from sqlalchemy import text

from .database import engine, init_db
from .log import setup_logging
from .mcp_server import build_mcp_app, mcp as mcp_server
from .query import query_string
from .routes import api as api_routes
from .routes import exports as exports_routes
from .routes import rfis as rfis_routes
from .routes import tokens as tokens_routes

setup_logging()


def _safe_db_url(url: str) -> str:
    """遮蔽連線字串裡的密碼，避免寫進日誌。"""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}" if ":" in creds else url


@asynccontextmanager
async def lifespan(app: FastAPI):
    from contextlib import AsyncExitStack
    stack = AsyncExitStack()
    await stack.__aenter__()
    # 先確認狀態目錄可寫，再碰資料庫；volume 沒掛好就直接啟動失敗
    prepare_storage(settings)
    await init_db()
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    # MCP 的 session manager 需要自己的 lifespan，掛進主 app 一起啟動
    await stack.enter_async_context(mcp_server.session_manager.run())
    logger.info(
        "SA RFI 平台啟動 | base_url={} | db={} | data_dir={} | upload_dir={} | dev_bypass={}",
        settings.APP_BASE_URL, _safe_db_url(settings.DATABASE_URL),
        settings.DATA_DIR, settings.UPLOAD_DIR, settings.DEV_AUTH_BYPASS,
    )
    if settings.is_sqlite:
        logger.info(
            "資料庫為 SQLite（{}）—— 只能單一副本執行；要跑多副本請改用 "
            "PostgreSQL（設定 DATABASE_URL）。", settings.SQLITE_PATH,
        )
    if settings.DEV_AUTH_BYPASS:
        logger.warning(
            "＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝\n"
            "  ⚠️  DEV_AUTH_BYPASS 已開啟：所有人免登入且擁有 {} 權限！\n"
            "  ⚠️  此模式僅限本機開發，正式環境務必設為 false。\n"
            "＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝",
            ",".join(settings.DEV_SCOPES),
        )
    else:
        if settings.jwks_url:
            logger.info("JWT 驗章將使用 Auth Center JWKS：{}", settings.jwks_url)
        elif not public_key_available():
            logger.warning(
                "未啟用 JWKS 且找不到本地公鑰 {}，SSO 登入將無法驗證 JWT。"
                "請設定 JWKS_URL/AUTH_CENTER_BASE_URL 以使用 JWKS，"
                "或複製 public.pem，或本機測試時設定 DEV_AUTH_BYPASS=true。",
                settings.PUBLIC_KEY_PATH,
            )
    yield
    await stack.aclose()
    await app.state.http_client.aclose()
    logger.info("SA RFI 平台關閉")


# 注意：不設 FastAPI root_path —— nginx 已用尾斜線 proxy_pass 剝掉前綴，
# app 在 /static、/rfis 等原始路徑服務即可；對外連結前綴由下方 ROOT 處理。
app = FastAPI(title="SA RFI 管理平台", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """基本安全 headers：防 MIME 嗅探與點擊劫持。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# 子路徑前綴；模板以 {{ root }} 串接所有內部連結，cookie 路徑也用它
ROOT = settings.ROOT_PATH
COOKIE_PATH = ROOT + "/" if ROOT else "/"

def qs_without(filters: dict, q: str, key: str, value: str) -> str:
    """列表頁的篩選標籤：產生「移除這一項條件」後的 query string。"""
    trimmed = {
        k: [v for v in vals if not (k == key and v == value)]
        for k, vals in filters.items()
    }
    return query_string(trimmed, q)


templates = Jinja2Templates(directory="templates")
templates.env.globals["settings"] = settings
templates.env.globals["fdisplay"] = fields_module.display
templates.env.globals["week_label"] = fields_module.week_label
templates.env.globals["root"] = ROOT
# 靜態資源版本：多副本部署時設 ASSET_VERSION（image tag / commit sha）讓各副本一致；
# 未設定則用啟動時間，重啟即可讓瀏覽器重新抓 CSS（避免快取舊樣式）
templates.env.globals["asset_ver"] = settings.ASSET_VERSION or str(int(time.time()))
templates.env.globals["qs_without"] = qs_without
app.mount("/static", StaticFiles(directory="static"), name="static")

# 讓兩個 router 共用同一個 templates 實例
rfis_routes.templates = templates
exports_routes.templates = templates
tokens_routes.templates = templates
app.include_router(rfis_routes.router)
app.include_router(exports_routes.router)
app.include_router(tokens_routes.router)
# 唯讀 JSON API（/api/v1）—— 認證接受 Auth Center JWT 或個人 API Token
app.include_router(api_routes.router)
# MCP server（/mcp）—— 認證用個人 API Token，工具與 API 共用同一套查詢邏輯
app.mount("/mcp", build_mcp_app())


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """瀏覽器導覽遇到 401（未登入/Token 過期）時導向登入頁；
    其餘錯誤在 HTML 情境顯示錯誤頁，API 情境回 JSON。"""
    accepts_html = (
        "text/html" in request.headers.get("accept", "")
        and not request.url.path.startswith("/api/")
    )
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and accepts_html:
        return RedirectResponse(f"{ROOT}/login", status_code=303)
    if accepts_html:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": exc.status_code, "detail": exc.detail, "user": None},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# ── 認證路由 ──────────────────────────────────────────────────

@app.get("/login")
async def login():
    """導向 Auth Center 登入頁。"""
    if settings.DEV_AUTH_BYPASS:
        return RedirectResponse(f"{ROOT}/", status_code=303)
    return RedirectResponse(settings.login_url, status_code=303)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = Query(...)):
    """接收 Auth Center 授權碼，換取 JWT 後存入 Cookie。"""
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        data = await exchange_code_for_token(client, code)
    except HTTPException as exc:
        logger.warning("授權碼換取 token 失敗：{}", exc.detail)
        # 授權碼失效等 → 重新登入
        return RedirectResponse(f"{ROOT}/login", status_code=303)

    # 僅供記錄；解碼失敗（如缺公鑰）不應影響設定 cookie 的流程
    try:
        payload = decode_token(data["access_token"]) or {}
    except Exception:
        payload = {}
    logger.info("使用者登入成功：{}", payload.get("sub", "unknown"))
    response = RedirectResponse(f"{ROOT}/", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=data["access_token"],
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=data.get("expires_in", 43200),
        path=COOKIE_PATH,
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(f"{ROOT}/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return response


@app.get("/healthz")
async def healthz():
    """Liveness：行程還活著就好，不碰任何外部相依。"""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness：資料庫連得上、狀態目錄可寫，才算可以接流量。

    容器平台用這支來決定要不要把流量導進來；volume 掉了或 DB 連不上時
    會回 503，而不是讓使用者撞到 500。
    """
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
    try:
        prepare_storage(settings)
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {e}"

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        logger.warning("readiness 檢查未通過：{}", checks)
    return JSONResponse(
        {"status": "ready" if ready else "not ready", "checks": checks},
        status_code=200 if ready else 503,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: dict | None = Depends(optional_user)):
    """首頁：未登入顯示登入頁，已登入導向 RFI 列表。"""
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"login_url": f"{ROOT}/login"}
        )
    return RedirectResponse(f"{ROOT}/rfis", status_code=303)
