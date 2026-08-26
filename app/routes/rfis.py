"""RFI CRUD、修改紀錄、列表篩選、Dashboard、附件處理。"""

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.datastructures import UploadFile

from ..auth import require_scopes, require_user
from ..config import settings
from ..database import get_session
from ..fields import (
    ALLOWED_ATTACHMENT_EXT,
    FILTER_KEYS,
    FILTER_LABELS,
    LIST_COLUMNS,
    RFI_FIELDS,
    FIELD_BY_KEY,
    clean_payload,
    diff_payload,
    display,
    grouped_fields,
    week_label,
)
from ..models import Attachment, Rfi, RfiRevision
from ..query import (
    SORTABLE,
    active_filter_count,
    filter_options,
    filter_rows,
    make_rows,
    parse_filters,
    query_string,
    sort_rows,
)

router = APIRouter()

ROOT = settings.ROOT_PATH  # 子路徑前綴（redirect 用）

# 由 main.py 注入共用的 Jinja2Templates 實例
templates: Jinja2Templates = None  # type: ignore


def _ctx(request: Request, user: dict | None, **extra) -> dict:
    """模板共用 context。"""
    scopes = set((user or {}).get("scopes", []))
    base = {
        "request": request,
        "user": user,
        "can_write": "write" in scopes,
        "can_admin": "admin" in scopes,
        "fields": RFI_FIELDS,
        "field_groups": grouped_fields(),
        "list_columns": [FIELD_BY_KEY[k] for k in LIST_COLUMNS],
        "filter_keys": FILTER_KEYS,
        "filter_labels": FILTER_LABELS,
    }
    base.update(extra)
    return base


# ── 資料存取輔助 ──────────────────────────────────────────────

async def load_rows(session: AsyncSession):
    """讀出全部 RFI 並算好顯示字串（列表、匯出、Dashboard 共用）。"""
    stmt = select(Rfi).options(selectinload(Rfi.attachments))
    return make_rows(list((await session.execute(stmt)).scalars().all()))


async def _get_rfi(session: AsyncSession, rfi_id: int, with_rel: bool = False) -> Rfi:
    stmt = select(Rfi).where(Rfi.id == rfi_id)
    if with_rel:
        stmt = stmt.options(
            selectinload(Rfi.revisions), selectinload(Rfi.attachments)
        )
    else:
        stmt = stmt.options(selectinload(Rfi.attachments))
    rfi = (await session.execute(stmt)).scalar_one_or_none()
    if rfi is None:
        raise HTTPException(404, "找不到此 RFI")
    return rfi


def _to_date(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def sync_columns(rfi: Rfi, data: dict) -> None:
    """把 data 內的常用欄位同步到資料表欄位（供查詢與統計）。"""
    rfi.data = data
    rfi.rfi_date = _to_date(data.get("rfi_date", ""))
    rfi.week = week_label(data.get("rfi_date", ""))
    rfi.client = data.get("client", "")
    rfi.vendor = data.get("vendor", "")
    rfi.product = data.get("product", "")
    rfi.status = data.get("status", "")
    rfi.ic = data.get("ic", "")


async def next_rfi_no(session: AsyncSession, week: str) -> str:
    """產生同一週內遞增的案件編號，如 R26W29-01。"""
    prefix = f"R{week or '000000'}-"
    existing = (await session.execute(
        select(Rfi.rfi_no).where(Rfi.rfi_no.like(f"{prefix}%"))
    )).scalars().all()
    used = [int(n.rsplit("-", 1)[-1]) for n in existing if n.rsplit("-", 1)[-1].isdigit()]
    return f"{prefix}{(max(used) + 1) if used else 1:02d}"


def validate(data: dict) -> str:
    """檢查必填欄位；回傳錯誤訊息（空字串表示通過）。"""
    missing = [
        f.label for f in RFI_FIELDS
        if f.required and not display(f, data)
    ]
    if missing:
        return "以下必填欄位尚未填寫：" + "、".join(missing)
    if data.get("rfi_date") and not _to_date(data["rfi_date"]):
        return "RFI 日期格式不正確，請使用日期選擇器輸入。"
    return ""


# ── 列表 ──────────────────────────────────────────────────────

@router.get("/rfis", response_class=HTMLResponse)
async def list_rfis(
    request: Request,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    q: str = Query(""),
    sort: str = Query("week"),
    dir: str = Query("desc"),
):
    rows = await load_rows(session)
    filters = parse_filters(request.query_params)
    options = filter_options(rows, filters, q)
    visible = sort_rows(filter_rows(rows, filters, q), sort, dir)

    return templates.TemplateResponse(
        request, "list.html",
        _ctx(request, user,
             rows=visible, total=len(rows), filters=filters, options=options,
             q=q, sort=sort if sort in SORTABLE else "week", dir=dir,
             active_filters=active_filter_count(filters, q),
             qs=query_string(filters, q),
             qs_full=query_string(filters, q, sort, dir)),
    )


# ── 新增 ──────────────────────────────────────────────────────

@router.get("/rfis/new", response_class=HTMLResponse)
async def new_rfi_form(
    request: Request,
    user: dict = Depends(require_scopes("write")),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "form.html",
        _ctx(request, user, rfi=None, mode="new",
             values={"rfi_date": date.today().isoformat(), "status": "評估中",
                     "mux": "None", "owner": user.get("name") or user.get("sub", "")},
             suggestions=await field_suggestions(session)),
    )


@router.post("/rfis/new")
async def create_rfi(
    request: Request,
    user: dict = Depends(require_scopes("write")),
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    data = clean_payload(form)
    note = (form.get("note") or "").strip()

    error = validate(data)
    if error:
        return templates.TemplateResponse(
            request, "form.html",
            _ctx(request, user, rfi=None, values=data, mode="new", note_value=note,
                 error=error, suggestions=await field_suggestions(session)),
            status_code=400,
        )

    rfi = Rfi(created_by=user["sub"], updated_by=user["sub"], version=1)
    sync_columns(rfi, data)
    rfi.rfi_no = await next_rfi_no(session, rfi.week)
    session.add(rfi)
    await session.flush()

    session.add(RfiRevision(
        rfi_id=rfi.id, version=1, data=data,
        changes=diff_payload({}, data), action="create",
        note=note or "建立 RFI", edited_by=user["sub"],
    ))
    uploaded = await _save_attachments(session, rfi, form, user)
    if uploaded:
        session.add(RfiRevision(
            rfi_id=rfi.id, version=1, data=data, changes=[],
            action="attachment", note="新增附件：" + "、".join(uploaded),
            edited_by=user["sub"],
        ))
    await session.commit()
    logger.info("建立 RFI {} {} / {} by {}（附件 {} 份）",
                rfi.rfi_no, rfi.client, rfi.vendor, user["sub"], len(uploaded))
    return RedirectResponse(f"{ROOT}/rfis/{rfi.id}", status_code=303)


# ── 詳情 ──────────────────────────────────────────────────────

@router.get("/rfis/{rfi_id}", response_class=HTMLResponse)
async def rfi_detail(
    rfi_id: int,
    request: Request,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    back: str = Query("", description="返回列表時要還原的篩選條件"),
):
    rfi = await _get_rfi(session, rfi_id, with_rel=True)
    return templates.TemplateResponse(
        request, "detail.html", _ctx(request, user, rfi=rfi, back=back)
    )


# ── 編輯 ──────────────────────────────────────────────────────

@router.get("/rfis/{rfi_id}/edit", response_class=HTMLResponse)
async def edit_rfi_form(
    rfi_id: int,
    request: Request,
    user: dict = Depends(require_scopes("write")),
    session: AsyncSession = Depends(get_session),
):
    rfi = await _get_rfi(session, rfi_id)
    return templates.TemplateResponse(
        request, "form.html",
        _ctx(request, user, rfi=rfi, values=rfi.data, mode="edit",
             base_version=rfi.version, baseline_json=json.dumps(rfi.data),
             suggestions=await field_suggestions(session)),
    )


@router.post("/rfis/{rfi_id}/edit")
async def update_rfi(
    rfi_id: int,
    request: Request,
    user: dict = Depends(require_scopes("write")),
    session: AsyncSession = Depends(get_session),
):
    rfi = await _get_rfi(session, rfi_id)
    form = await request.form()
    submitted = clean_payload(form)          # 使用者送出的完整表單
    note = (form.get("note") or "").strip()
    try:
        baseline = json.loads(form.get("baseline") or "{}")  # 開啟編輯頁當下的資料
    except json.JSONDecodeError:
        baseline = {}

    current = rfi.data                        # 目前資料庫最新（可能已被他人更新）

    def reshow(error: str, status_code: int, **extra):
        return templates.TemplateResponse(
            request, "form.html",
            _ctx(request, user, rfi=rfi, values=submitted, mode="edit",
                 base_version=rfi.version, baseline_json=json.dumps(current),
                 note_value=note, error=error, suggestions=suggestions, **extra),
            status_code=status_code,
        )

    suggestions = await field_suggestions(session)

    # 只取使用者「實際改動」的欄位（相對於他開啟時的 baseline）
    user_changed = [f for f in RFI_FIELDS
                    if display(f, submitted) != display(f, baseline)]

    # 衝突：使用者改的欄位，在他編輯期間又被別人改成不同值
    conflicts = [f for f in user_changed
                 if display(f, current) != display(f, baseline)
                 and display(f, current) != display(f, submitted)]

    if conflicts:
        logger.warning("RFI #{} 編輯衝突 by {}：{}",
                       rfi.id, user["sub"], [f.key for f in conflicts])
        return reshow("", 409, conflicts=[
            {"label": f.label, "theirs": display(f, current), "mine": display(f, submitted)}
            for f in conflicts
        ])

    # 欄位層級合併：把使用者改動套到目前最新資料上（不覆蓋他人其他欄位）
    merged = dict(current)
    for f in user_changed:
        merged[f.key] = submitted[f.key]

    error = validate(merged)
    if error:
        return reshow(error, 400)

    changes = diff_payload(current, merged)

    # 欄位有實際變動時，修改說明必填（附件上傳會自動記錄，不在此限）
    if changes and not note:
        return reshow("請填寫本次修改說明（欄位有變動時為必填）。", 400)

    has_uploads = any(
        isinstance(u, UploadFile) and u.filename for u in form.getlist("attachments")
    )

    if not changes and not has_uploads:
        return RedirectResponse(f"{ROOT}/rfis/{rfi.id}", status_code=303)

    # 樂觀鎖：以「載入時的版本」為條件做原子更新。
    # 若兩人幾乎同時提交，先提交者讓 version 遞增，後提交者的條件 version
    # 已不符 → 影響 0 筆 → 視為衝突，警示並請使用者重新整理。
    loaded_version = rfi.version
    new_version = loaded_version + 1
    values = {"version": new_version, "updated_by": user["sub"]}
    if changes:
        probe = Rfi()
        sync_columns(probe, merged)
        values.update(
            data=merged, rfi_date=probe.rfi_date, week=probe.week,
            client=probe.client, vendor=probe.vendor, product=probe.product,
            status=probe.status, ic=probe.ic,
        )
    result = await session.execute(
        sa_update(Rfi)
        .where(Rfi.id == rfi_id, Rfi.version == loaded_version)
        .values(**values)
    )
    if result.rowcount == 0:
        await session.rollback()
        logger.warning("RFI #{} 並行更新衝突（版本已變動），要求 {} 重新整理",
                       rfi_id, user["sub"])
        rfi = await _get_rfi(session, rfi_id)
        current = rfi.data
        return reshow(
            "此 RFI 剛被其他人更新，為避免覆蓋對方的變更，請重新整理頁面後再編輯。", 409
        )

    if changes:
        session.add(RfiRevision(
            rfi_id=rfi_id, version=new_version, data=merged,
            changes=changes, action="update",
            note=note or "更新 RFI", edited_by=user["sub"],
        ))
        logger.info("更新 RFI #{} by {}（合併 {} 欄）", rfi_id, user["sub"], len(changes))

    if has_uploads:
        rfi.data = merged
        uploaded = await _save_attachments(session, rfi, form, user)
        if uploaded:
            session.add(RfiRevision(
                rfi_id=rfi_id, version=new_version, data=merged,
                changes=[], action="attachment",
                note="新增附件：" + "、".join(uploaded), edited_by=user["sub"],
            ))
    await session.commit()
    return RedirectResponse(f"{ROOT}/rfis/{rfi.id}", status_code=303)


@router.post("/rfis/{rfi_id}/delete")
async def delete_rfi(
    rfi_id: int,
    user: dict = Depends(require_scopes("admin")),
    session: AsyncSession = Depends(get_session),
):
    rfi = await _get_rfi(session, rfi_id)
    for att in rfi.attachments:
        _remove_file(att.stored_name)
    await session.delete(rfi)
    await session.commit()
    logger.warning("刪除 RFI {} {} / {} by {}",
                   rfi.rfi_no, rfi.client, rfi.vendor, user["sub"])
    return RedirectResponse(f"{ROOT}/rfis", status_code=303)


# ── 修改紀錄 ──────────────────────────────────────────────────

@router.get("/rfis/{rfi_id}/history", response_class=HTMLResponse)
async def rfi_history(
    rfi_id: int,
    request: Request,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    rfi = await _get_rfi(session, rfi_id, with_rel=True)
    return templates.TemplateResponse(
        request, "history.html", _ctx(request, user, rfi=rfi)
    )


# ── 既有值建議（combo 欄位的 datalist）──────────────────────────

async def field_suggestions(session: AsyncSession) -> dict[str, list[str]]:
    """把資料庫既有值併入 combo 欄位的建議清單，讓輸入越用越省事。"""
    rows = (await session.execute(select(Rfi.data))).scalars().all()
    suggestions: dict[str, list[str]] = {}
    for f in RFI_FIELDS:
        if f.type != "combo":
            continue
        seen = list(f.options)
        for data in rows:
            val = (data or {}).get(f.key, "")
            if isinstance(val, str) and val and val not in seen:
                seen.append(val)
        suggestions[f.key] = seen
    return suggestions


# ── Dashboard ─────────────────────────────────────────────────

_PIE_PALETTE = [
    "#1a73e8", "#1e8e3e", "#e8710a", "#9334e6", "#d93025",
    "#12b5cb", "#f9ab00", "#5f6368", "#a142f4", "#0b8043",
]

# Dashboard 可切換的統計維度
DASH_GROUPS = [
    ("week", "週別"), ("product", "產品類別"), ("vendor", "面板廠"),
    ("client", "終端客戶"), ("status", "處理狀態"), ("owner", "負責 SA"),
]
DASH_GROUP_LABELS = dict(DASH_GROUPS)


def _pie(counter: Counter) -> dict:
    """把計數轉成圓餅圖資料（含 conic-gradient 字串與圖例）。"""
    total = sum(counter.values())
    segments = []
    acc = 0.0
    for i, (label, count) in enumerate(counter.most_common()):
        pct = (count / total * 100) if total else 0
        color = _PIE_PALETTE[i % len(_PIE_PALETTE)]
        segments.append({
            "label": label, "count": count, "pct": round(pct, 1),
            "color": color, "start": round(acc, 3), "end": round(acc + pct, 3),
        })
        acc += pct
    gradient = ", ".join(f"{s['color']} {s['start']}% {s['end']}%" for s in segments)
    return {"total": total, "segments": segments, "gradient": gradient}


def dashboard_stats(rows, group: str, year: str) -> dict:
    """依指定維度統計，回傳表格列（含占比）與圓餅圖資料。"""
    if year:
        rows = [r for r in rows if (r.obj.rfi_date and str(r.obj.rfi_date.year) == year)]
    counter = Counter((r.v.get(group) or "（未填）") for r in rows)
    total = sum(counter.values())
    # 週別依時間排序，其餘依筆數多寡排序
    if group == "week":
        ordered = sorted(counter.items(), key=lambda kv: kv[0])
    else:
        ordered = counter.most_common()
    table = [
        {"category": k, "count": v, "pct": round(v / total * 100, 1) if total else 0.0}
        for k, v in ordered
    ]
    return {"total": total, "table": table, "pie": _pie(counter), "rows": rows}


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    group: str = Query("week"),
    year: str = Query(""),
):
    if group not in DASH_GROUP_LABELS:
        group = "week"
    rows = await load_rows(session)
    years = sorted({str(r.obj.rfi_date.year) for r in rows if r.obj.rfi_date}, reverse=True)
    stats = dashboard_stats(rows, group, year)
    scoped = stats["rows"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent = 0
    for r in scoped:
        dt = r.obj.updated_at
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            recent += 1
    open_cases = sum(1 for r in scoped if r.obj.status not in ("已結案", "暫停"))

    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx(request, user,
             group=group, group_label=DASH_GROUP_LABELS[group], groups=DASH_GROUPS,
             year=year, years=years, stats=stats,
             total=stats["total"], open_cases=open_cases, recent=recent,
             num_clients=len({r.obj.client for r in scoped if r.obj.client}),
             num_vendors=len({r.obj.vendor for r in scoped if r.obj.vendor}),
             pie_status=_pie(Counter((r.obj.status or "（未填）") for r in scoped))),
    )


# ── 附件下載 / 刪除 ────────────────────────────────────────────

# 依副檔名決定回應的 media type（安全性：絕不信任上傳者宣告的 Content-Type，
# 否則可上傳偽裝成 .pdf 的 text/html 造成同網域 XSS）
_MEDIA_TYPE_BY_EXT = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
# 可安全地在瀏覽器內嵌開啟的型別（其餘一律當附件下載）
_INLINE_EXT = {".pdf", ".png", ".jpg", ".jpeg"}


@router.get("/attachments/{att_id}")
async def download_attachment(
    att_id: int,
    user: dict = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    att = (await session.execute(
        select(Attachment).where(Attachment.id == att_id)
    )).scalar_one_or_none()
    if att is None:
        raise HTTPException(404, "找不到附件")
    path = settings.UPLOAD_DIR / att.stored_name
    if not path.exists():
        raise HTTPException(404, "附件檔案不存在")
    ext = Path(att.filename).suffix.lower()
    media_type = _MEDIA_TYPE_BY_EXT.get(ext, "application/octet-stream")
    return FileResponse(
        path,
        filename=att.filename,
        media_type=media_type,
        content_disposition_type="inline" if ext in _INLINE_EXT else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/attachments/{att_id}/delete")
async def delete_attachment(
    att_id: int,
    user: dict = Depends(require_scopes("write")),
    session: AsyncSession = Depends(get_session),
):
    att = (await session.execute(
        select(Attachment).where(Attachment.id == att_id)
    )).scalar_one_or_none()
    if att is None:
        raise HTTPException(404, "找不到附件")
    rfi_id = att.rfi_id
    filename = att.filename
    rfi = await _get_rfi(session, rfi_id)
    _remove_file(att.stored_name)
    await session.delete(att)
    session.add(RfiRevision(
        rfi_id=rfi_id, version=rfi.version, data=rfi.data, changes=[],
        action="attachment", note=f"移除附件：{filename}", edited_by=user["sub"],
    ))
    rfi.updated_by = user["sub"]
    await session.commit()
    logger.info("刪除附件 {} by {}", att.stored_name, user["sub"])
    return RedirectResponse(f"{ROOT}/rfis/{rfi_id}/edit", status_code=303)


# ── 附件儲存輔助 ──────────────────────────────────────────────

def _sanitize(name: str) -> str:
    """移除路徑成分並將不安全字元換成底線（保留中英數字與 . - _）。"""
    name = Path(name).name
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return name or "file"


def _rfi_folder(rfi: Rfi) -> str:
    """以「RFI編號_終端客戶」組出子資料夾名稱。"""
    raw = f"{rfi.rfi_no or rfi.id}_{(rfi.data or {}).get('client', '')}"
    folder = re.sub(r"[^\w.\-]+", "_", raw, flags=re.UNICODE).strip("._")
    return folder or "rfi"


async def _save_attachments(session, rfi: Rfi, form, user: dict) -> list[str]:
    """儲存表單附件到 UPLOAD_DIR/<編號_客戶>/ 內，回傳已儲存的檔名清單。"""
    folder = _rfi_folder(rfi)
    dest_dir = settings.UPLOAD_DIR / folder
    saved: list[str] = []
    for upload in form.getlist("attachments"):
        if not isinstance(upload, UploadFile) or not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_ATTACHMENT_EXT:
            allowed = "／".join(
                sorted(e.lstrip(".").upper() for e in ALLOWED_ATTACHMENT_EXT)
            )
            raise HTTPException(400, f"不支援的附件格式：{ext}（僅限 {allowed}）")
        content = await upload.read()
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(
                400, f"附件「{upload.filename}」超過 {settings.MAX_UPLOAD_MB}MB 上限"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe = _sanitize(upload.filename)
        stem, suffix = Path(safe).stem, Path(safe).suffix
        target = dest_dir / safe
        i = 1
        while target.exists():  # 避免同資料夾內同名覆蓋
            safe = f"{stem}_{i}{suffix}"
            target = dest_dir / safe
            i += 1
        target.write_bytes(content)
        session.add(Attachment(
            rfi_id=rfi.id, filename=upload.filename,
            stored_name=f"{folder}/{safe}",
            content_type=upload.content_type or "", size=len(content),
            uploaded_by=user["sub"],
        ))
        logger.info("上傳附件 {}/{} ({} bytes) by {}",
                    folder, safe, len(content), user["sub"])
        saved.append(upload.filename)
    return saved


def _remove_file(stored_name: str) -> None:
    try:
        p = settings.UPLOAD_DIR / stored_name
        p.unlink(missing_ok=True)
        parent = p.parent
        if (parent != settings.UPLOAD_DIR and parent.exists()
                and not any(parent.iterdir())):
            parent.rmdir()  # 移除已清空的 RFI 資料夾
    except OSError:
        pass
