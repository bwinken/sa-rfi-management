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
from .config import settings
from .database import init_db
from .log import setup_logging
from .query import query_string
from .routes import exports as exports_routes
from .routes import rfis as rfis_routes

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    logger.info(
        "SA RFI 平台啟動 | base_url={} | db={} | upload_dir={} | dev_bypass={}",
        settings.APP_BASE_URL, settings.DATABASE_URL,
        settings.UPLOAD_DIR, settings.DEV_AUTH_BYPASS,
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
# 啟動時間作為靜態資源版本，重啟即可讓瀏覽器重新抓 CSS（避免快取舊樣式）
templates.env.globals["asset_ver"] = str(int(time.time()))
templates.env.globals["qs_without"] = qs_without
app.mount("/static", StaticFiles(directory="static"), name="static")

# 讓兩個 router 共用同一個 templates 實例
rfis_routes.templates = templates
exports_routes.templates = templates
app.include_router(rfis_routes.router)
app.include_router(exports_routes.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """瀏覽器導覽遇到 401（未登入/Token 過期）時導向登入頁；
    其餘錯誤在 HTML 情境顯示錯誤頁，API 情境回 JSON。"""
    accepts_html = "text/html" in request.headers.get("accept", "")
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
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: dict | None = Depends(optional_user)):
    """首頁：未登入顯示登入頁，已登入導向 RFI 列表。"""
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"login_url": f"{ROOT}/login"}
        )
    return RedirectResponse(f"{ROOT}/rfis", status_code=303)
