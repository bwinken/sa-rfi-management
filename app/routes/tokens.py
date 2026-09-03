"""個人 API Token 的管理介面。

Auth Center 的 JWT 只有 12 小時且沒有 refresh，放進腳本或排程設定
會變成每天都要重新貼一次。這裡讓使用者自行簽發長期有效的唯讀 token，
用完或外洩隨時可以撤銷。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import generate_token, require_user
from ..config import settings
from ..database import get_session
from ..models import ApiToken
from .rfis import _ctx

router = APIRouter()

ROOT = settings.ROOT_PATH
templates: Jinja2Templates = None  # type: ignore

# 可選的有效期（天）；0 代表不設期限
EXPIRY_CHOICES = [30, 90, 180, 365, 0]
DEFAULT_EXPIRY_DAYS = 90


async def _my_tokens(session: AsyncSession, owner: str) -> list[ApiToken]:
    return list((await session.execute(
        select(ApiToken)
        .where(ApiToken.owner == owner)
        .order_by(ApiToken.id.desc())
    )).scalars().all())


@router.get("/tokens", response_class=HTMLResponse)
async def list_tokens(
    request: Request,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "tokens.html",
        _ctx(request, user,
             tokens=await _my_tokens(session, user["sub"]),
             expiry_choices=EXPIRY_CHOICES,
             default_expiry=DEFAULT_EXPIRY_DAYS,
             now=datetime.now(timezone.utc)),
    )


@router.post("/tokens", response_class=HTMLResponse)
async def create_token(
    request: Request,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    name = (form.get("name") or "").strip()[:128]
    try:
        days = int(form.get("expires_days") or DEFAULT_EXPIRY_DAYS)
    except ValueError:
        days = DEFAULT_EXPIRY_DAYS
    if days not in EXPIRY_CHOICES:
        days = DEFAULT_EXPIRY_DAYS

    if not name:
        return templates.TemplateResponse(
            request, "tokens.html",
            _ctx(request, user, tokens=await _my_tokens(session, user["sub"]),
                 expiry_choices=EXPIRY_CHOICES, default_expiry=DEFAULT_EXPIRY_DAYS,
                 now=datetime.now(timezone.utc),
                 error="請填寫用途說明，之後才認得出這支 token 是給誰用的。"),
            status_code=400,
        )

    raw, prefix, token_hash = generate_token()
    row = ApiToken(
        name=name, prefix=prefix, token_hash=token_hash, scopes="read",
        owner=user["sub"],
        expires_at=(datetime.now(timezone.utc) + timedelta(days=days)) if days else None,
    )
    session.add(row)
    await session.commit()
    logger.info("建立 API Token {}…（{}）by {}，效期 {}",
                prefix, name, user["sub"], f"{days} 天" if days else "無期限")

    # 完整 token 只在這一次回應中出現，之後資料庫只留雜湊
    return templates.TemplateResponse(
        request, "tokens.html",
        _ctx(request, user, tokens=await _my_tokens(session, user["sub"]),
             expiry_choices=EXPIRY_CHOICES, default_expiry=DEFAULT_EXPIRY_DAYS,
             now=datetime.now(timezone.utc), new_token=raw, new_token_name=name),
    )


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(
    token_id: int,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(
        select(ApiToken).where(ApiToken.id == token_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "找不到這支 token")
    # 只能撤銷自己的
    if row.owner != user["sub"]:
        raise HTTPException(403, "只能撤銷自己建立的 token")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        logger.warning("撤銷 API Token {}…（{}）by {}", row.prefix, row.name, user["sub"])
    return RedirectResponse(f"{ROOT}/tokens", status_code=303)
