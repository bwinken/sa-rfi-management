"""唯讀 JSON API（/api/v1）。

刻意只提供讀取：建立與修改 RFI 一律走網頁介面，才會有完整的
修改說明與逐欄位紀錄。這也讓 token 外流的影響限縮在「資料被看到」，
而不會變成「資料被竄改」。

認證方式（擇一）：
  Authorization: Bearer <Auth Center JWT>     — 12 小時效期，適合互動式取得
  Authorization: Bearer sarfi_xxxxxxxx...     — 個人 API Token，長期有效可撤銷
瀏覽器 Cookie 也接受，方便直接在分頁裡打開 API 看結果。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import _bearer, optional_user, resolve_api_token
from ..database import get_session
from ..fields import (
    FILTER_KEYS,
    FILTER_LABELS,
    LIST_COLUMNS,
    RFI_FIELDS,
)
from ..query import (
    SORTABLE,
    filter_options,
    filter_rows,
    parse_filters,
    sort_rows,
)
from .rfis import DASH_GROUP_LABELS, dashboard_stats, load_rows, _get_rfi

router = APIRouter(prefix="/api/v1", tags=["api"])

MAX_LIMIT = 500


async def api_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
    cookie_user: Annotated[dict | None, Depends(optional_user)] = None,
) -> dict:
    """API 的認證：個人 API Token 優先，其次 JWT / Cookie。"""
    raw = _bearer(request.headers.get("authorization"))
    if raw and raw.startswith("sarfi_"):
        user = await resolve_api_token(session, raw)
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "API Token 無效、已過期或已撤銷。",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        user = cookie_user
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "需要認證：請帶上 Authorization: Bearer <token>。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if "read" not in set(user.get("scopes", [])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "權限不足，缺少 read。")
    return user


Principal = Annotated[dict, Depends(api_principal)]


def _serialize(row, *, detail: bool = False) -> dict:
    """把一筆 RFI 轉成 JSON。

    同時給出原始值（values）與顯示字串（display）：
    前者方便程式運算，後者與網頁、投影片上看到的完全一致。
    """
    r = row.obj
    data = {
        "id": r.id,
        "rfi_no": r.rfi_no,
        "week": r.week,
        "rfi_date": r.rfi_date.isoformat() if r.rfi_date else None,
        "values": {f.key: (r.data or {}).get(f.key) for f in RFI_FIELDS},
        "display": {k: v for k, v in row.v.items()},
        "attachments": [
            {"id": a.id, "filename": a.filename, "size": a.size,
             "uploaded_by": a.uploaded_by,
             "uploaded_at": a.uploaded_at.isoformat() if a.uploaded_at else None}
            for a in r.attachments
        ],
        "created_by": r.created_by,
        "updated_by": r.updated_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "version": r.version,
    }
    return data


def _serialize_revision(rev) -> dict:
    return {
        "version": rev.version,
        "action": rev.action,
        "note": rev.note,
        "edited_by": rev.edited_by,
        "edited_at": rev.edited_at.isoformat() if rev.edited_at else None,
        "changes": rev.changes,
    }


@router.get("/me")
async def whoami(user: Principal) -> dict:
    """確認目前這組憑證是誰、有什麼權限 —— 接通時第一支該打的 API。"""
    return {
        "sub": user.get("sub"),
        "name": user.get("name"),
        "scopes": user.get("scopes", []),
        "auth": user.get("auth", "jwt"),
    }


@router.get("/fields")
async def list_fields(user: Principal) -> dict:
    """欄位定義：讓 client 知道有哪些欄位、型別與可選值。"""
    return {
        "fields": [
            {"key": f.key, "label": f.label, "short": f.head, "type": f.type,
             "required": f.required, "group": f.group, "unit": f.unit,
             "options": f.options, "info": f.info}
            for f in RFI_FIELDS
        ],
        "list_columns": LIST_COLUMNS,
        "filter_keys": FILTER_KEYS,
        "filter_labels": FILTER_LABELS,
        "sortable": SORTABLE,
    }


@router.get("/rfis")
async def list_rfis(
    request: Request,
    user: Principal,
    session: AsyncSession = Depends(get_session),
    q: str = Query("", description="全欄位關鍵字"),
    sort: str = Query("week"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict:
    """RFI 列表。篩選參數與網頁完全相同，可重複帶：

        /api/v1/rfis?vendor=BOE&vendor=AUO&product=OLED+NB&sort=size&dir=asc

    可用的篩選欄位見 /api/v1/fields 的 filter_keys。
    """
    rows = await load_rows(session)
    filters = parse_filters(request.query_params)
    matched = sort_rows(filter_rows(rows, filters, q), sort, dir)
    page = matched[offset:offset + limit]
    return {
        "total": len(matched),
        "limit": limit,
        "offset": offset,
        "filters": {k: v for k, v in filters.items() if v},
        "items": [_serialize(r) for r in page],
    }


@router.get("/rfis/{rfi_id}")
async def get_rfi(
    rfi_id: int,
    user: Principal,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """單筆 RFI，含完整修改紀錄。"""
    from ..query import make_rows

    rfi = await _get_rfi(session, rfi_id, with_rel=True)
    row = make_rows([rfi])[0]
    payload = _serialize(row, detail=True)
    payload["revisions"] = [_serialize_revision(rev) for rev in rfi.revisions]
    return payload


@router.get("/filters")
async def list_filter_options(
    request: Request,
    user: Principal,
    session: AsyncSession = Depends(get_session),
    q: str = Query(""),
) -> dict:
    """各篩選欄位目前可選的值與筆數（會依已帶入的其他條件交叉收斂）。"""
    rows = await load_rows(session)
    filters = parse_filters(request.query_params)
    return {"options": filter_options(rows, filters, q)}


@router.get("/stats")
async def stats(
    user: Principal,
    session: AsyncSession = Depends(get_session),
    group: str = Query("week", description="week / product / vendor / client / status / owner"),
    year: str = Query(""),
) -> dict:
    """Dashboard 的統計數字。"""
    if group not in DASH_GROUP_LABELS:
        raise HTTPException(
            400,
            f"不支援的 group：{group}（可用：{', '.join(DASH_GROUP_LABELS)}）",
        )
    rows = await load_rows(session)
    result = dashboard_stats(rows, group, year)
    scoped = result["rows"]
    return {
        "group": group,
        "group_label": DASH_GROUP_LABELS[group],
        "year": year or None,
        "total": result["total"],
        "open_cases": sum(1 for r in scoped if r.obj.status not in ("已結案", "暫停")),
        "breakdown": result["table"],
    }
