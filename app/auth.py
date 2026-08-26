"""Auth Center SSO 整合。

流程（與 Auth Center example_app 一致）：
    1. 未登入 → 導向 {AUTH_CENTER}/auth/login?app_id=..&redirect_uri=..
    2. 登入成功 → Auth Center 303 回 callback 並帶 ?code=xxx
    3. 後端用 code + client_secret 向 /auth/token 換取 RS256 JWT
    4. JWT 存入 httponly Cookie，後續以公鑰驗證

JWT payload 欄位：sub（員工帳號）、name、org_id、scopes（read/write/admin）。
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

import httpx
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status
from loguru import logger

from .config import settings

COOKIE_NAME = "access_token"


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


def optional_user(
    access_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> dict | None:
    """取得目前使用者，未登入回傳 None（不丟錯）。"""
    if settings.DEV_AUTH_BYPASS:
        return _dev_user()
    if not access_token:
        return None
    return decode_token(access_token)


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
