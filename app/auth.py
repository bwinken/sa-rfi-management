"""Auth Center SSO 整合。

流程（與 Auth Center example_app 一致）：
    1. 未登入 → 導向 {AUTH_CENTER}/auth/login?app_id=..&redirect_uri=..
    2. 登入成功 → Auth Center 303 回 callback 並帶 ?code=xxx
    3. 後端用 code + client_secret 向 /auth/token 換取 RS256 JWT
    4. JWT 存入 httponly Cookie，後續以公鑰驗證

JWT payload 欄位：sub（員工帳號）、name、org_id、scopes（read/write/admin）。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import httpx
import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from .config import settings

COOKIE_NAME = "access_token"
# 個人 API Token 的前綴，方便在日誌／secret scanning 中辨識
TOKEN_PREFIX = "sarfi_"
TOKEN_PREVIEW_LEN = 8


@lru_cache
def _public_key() -> str:
    return Path(settings.PUBLIC_KEY_PATH).read_text()


def public_key_available() -> bool:
    """公鑰檔是否存在（供啟動時檢查與提示）。"""
    return Path(settings.PUBLIC_KEY_PATH).is_file()


async def exchange_code_for_token(client: httpx.AsyncClient, code: str) -> dict:
    """以授權碼換取 JWT。回傳 Auth Center /auth/token 的 JSON。"""
    resp = await client.post(
        f"{settings.AUTH_CENTER_BASE_URL}/auth/token",
        json={
            "code": code,
            "app_id": settings.APP_ID,
            "client_secret": settings.CLIENT_SECRET,
        },
    )
    if resp.status_code != 200:
        try:
            error = resp.json().get("error", "unknown")
        except Exception:
            error = "unknown"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Token 交換失敗：{error}")
    return resp.json()


@lru_cache
def _jwk_client() -> "jwt.PyJWKClient":
    return jwt.PyJWKClient(settings.jwks_url)


def _candidate_keys(token: str) -> list:
    """取得可用於驗章的公鑰候選清單。

    優先順序：JWKS（依 kid 對應；取不到 kid 則取全部金鑰）→ 本地 public.pem。
    """
    keys: list = []
    if settings.jwks_url:
        try:
            client = _jwk_client()
            try:
                keys.append(client.get_signing_key_from_jwt(token).key)
            except Exception:
                keys.extend(k.key for k in client.get_signing_keys())
        except Exception as e:
            logger.debug("JWKS（{}）取得金鑰失敗，改用本地公鑰：{}", settings.jwks_url, e)
    try:
        keys.append(_public_key())
    except OSError:
        pass
    return keys


def decode_token(token: str) -> dict | None:
    """驗證並解碼 JWT，失敗回傳 None。"""
    keys = _candidate_keys(token)
    if not keys:
        logger.error(
            "無可用驗章公鑰：JWKS（{}）取得失敗且找不到本地 {}。"
            "請確認可連到 Auth Center，或複製 public.pem，"
            "或本機測試時設定 DEV_AUTH_BYPASS=true。",
            settings.jwks_url or "(停用)", settings.PUBLIC_KEY_PATH,
        )
        return None

    last_err: Exception | None = None
    for key in keys:
        try:
            return jwt.decode(
                token, key,
                algorithms=[settings.ALGORITHM],
                audience=settings.APP_ID,
                issuer=settings.AUTH_CENTER_BASE_URL,
            )
        except jwt.InvalidSignatureError as e:
            last_err = e  # 可能是別把金鑰簽的 → 換下一把
            continue
        except jwt.PyJWTError as e:
            last_err = e  # iss/aud/exp 等錯誤換金鑰也沒用
            break

    # 解出未驗證的 claims 以利對照（不信任內容，僅供診斷）
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        unverified = {}
    logger.warning(
        "JWT 驗證失敗：{} | 預期 iss={!r} aud={!r}；token 實際 iss={!r} aud={!r} sub={!r}",
        type(last_err).__name__ + (f": {last_err}" if str(last_err) else ""),
        settings.AUTH_CENTER_BASE_URL, settings.APP_ID,
        unverified.get("iss"), unverified.get("aud"), unverified.get("sub"),
    )
    return None


def _dev_user() -> dict:
    return {
        "sub": settings.DEV_USER,
        "name": settings.DEV_USER,
        "org_id": "DEV",
        "scopes": settings.DEV_SCOPES,
    }


def _bearer(authorization: str | None) -> str | None:
    """取出 Authorization: Bearer <token> 的內容。"""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def optional_user(
    access_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict | None:
    """取得目前使用者，未登入回傳 None（不丟錯）。

    支援兩種憑證：
      1. 瀏覽器的 httponly Cookie（網頁介面）
      2. Authorization: Bearer <Auth Center JWT>（程式化呼叫）

    個人 API Token 走另一條路（需要查資料庫），見 api_principal()。
    """
    if settings.DEV_AUTH_BYPASS:
        return _dev_user()
    token = access_token or _bearer(authorization)
    if not token:
        return None
    return decode_token(token)


# ── 個人 API Token ────────────────────────────────────────────

def hash_token(raw: str) -> str:
    """Token 只存雜湊。這是高熵隨機值，不需要 KDF；SHA-256 足夠且快。"""
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """產生新 token，回傳 (完整token, 前綴預覽, 雜湊)。完整值只會出現這一次。"""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    body = raw[len(TOKEN_PREFIX):]
    return raw, body[:TOKEN_PREVIEW_LEN], hash_token(raw)


async def resolve_api_token(session: "AsyncSession", raw: str) -> dict | None:
    """驗證個人 API Token，通過則回傳與 JWT 相同形狀的 user dict。"""
    from .models import ApiToken  # 延後 import，避免與 models 互相載入

    if not raw.startswith(TOKEN_PREFIX):
        return None
    row = (await session.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_token(raw))
    )).scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(timezone.utc)
    if row.revoked_at is not None:
        logger.warning("已撤銷的 API Token 嘗試存取：{}…（擁有者 {}）", row.prefix, row.owner)
        return None
    if row.expires_at is not None:
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            logger.info("已過期的 API Token 嘗試存取：{}…（擁有者 {}）", row.prefix, row.owner)
            return None

    # 最後使用時間節流更新：每次請求都寫一筆太浪費，5 分鐘內不重複寫
    last = row.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or (now - last) > timedelta(minutes=5):
        row.last_used_at = now
        await session.commit()

    return {
        "sub": row.owner,
        "name": f"{row.owner}（API Token：{row.name or row.prefix}）",
        "org_id": "",
        "scopes": row.scope_list,
        "auth": "api_token",
        "token_id": row.id,
    }


def require_user(user: Annotated[dict | None, Depends(optional_user)]) -> dict:
    """要求已登入，否則 401（前端會據此導向登入頁）。"""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登入，請先透過 Auth Center 登入。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_scopes(*required: str):
    """Dependency factory：要求使用者具備指定 scopes。"""

    def _checker(user: Annotated[dict, Depends(require_user)]) -> dict:
        have = set(user.get("scopes", []))
        missing = set(required) - have
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"權限不足，缺少：{', '.join(sorted(missing))}",
            )
        return user

    return _checker


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client
