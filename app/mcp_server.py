"""MCP server —— 掛在同一個 FastAPI app 的 /mcp 底下。

只提供讀取工具，與 /api/v1 共用同一套篩選規則（app/query.py），
所以 Claude 看到的資料和 SA 在網頁上看到的一定一致。

認證：Authorization: Bearer <個人 API Token>，由下方的 middleware 驗證。
這裡刻意不使用 SDK 的 OAuth 機制 —— 我們目前沒有實作授權伺服器，
不應該對外宣告自己有。之後若要改成完整 OAuth 流程，換掉這一層即可，
工具本身不用動。
"""

from typing import Annotated, Any

from loguru import logger
from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import _bearer, resolve_api_token
from .config import settings
from .database import SessionLocal
from .fields import FILTER_KEYS, LIST_COLUMNS, RFI_FIELDS
from .query import filter_options, filter_rows, make_rows, sort_rows
from .routes.rfis import DASH_GROUP_LABELS, dashboard_stats, load_rows

INSTRUCTIONS = """\
這裡是 SA 團隊的客戶 RFI（Request For Information）資料庫。
每筆 RFI 是一次客戶詢價／技術評估請求，含終端客戶、面板廠、IC 型號、
面板規格（尺寸／解析度／頻率）、處理狀態與評估事項。

日期以「週別」表示，格式為 26W35（2026 年第 35 週）。
案件編號格式為 R26W35-01。

這些工具只能讀取。新增或修改 RFI 必須由 SA 在網頁上操作，
因為那裡才會留下修改說明與逐欄位的異動紀錄。
"""

mcp = MCPServer(
    name="sa-rfi",
    title="SA RFI 管理平台",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)


# ── 工具 ──────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "搜尋 RFI。所有篩選條件皆為選填且可多值（例如 vendor=['BOE','AUO'] "
        "表示這兩家任一）。不確定某欄位有哪些值時，先用 list_filter_values。"
    )
)
async def search_rfis(
    query: Annotated[str, Field(description="全欄位關鍵字，例如 IC 型號或客戶名")] = "",
    week: Annotated[list[str], Field(description="週別，如 ['26W35']")] = [],
    client: Annotated[list[str], Field(description="終端客戶，如 ['HP']")] = [],
    vendor: Annotated[list[str], Field(description="面板廠，如 ['BOE']")] = [],
    product: Annotated[list[str], Field(description="產品類別，如 ['OLED NB']")] = [],
    status: Annotated[list[str], Field(description="處理狀態，如 ['評估中']")] = [],
    ic: Annotated[list[str], Field(description="IC 型號")] = [],
    sort: Annotated[str, Field(description="排序欄位，預設 week")] = "week",
    order: Annotated[str, Field(description="asc 或 desc")] = "desc",
    limit: Annotated[int, Field(description="最多回傳幾筆（1-100）", ge=1, le=100)] = 20,
) -> dict[str, Any]:
    filters = {
        "week": week, "client": client, "vendor": vendor,
        "product": product, "status": status, "ic": ic, "size": [],
    }
    async with SessionLocal() as session:
        rows = await load_rows(session)
    matched = sort_rows(filter_rows(rows, filters, query), sort, order)
    items = [
        {"rfi_no": r.obj.rfi_no, "week": r.obj.week,
         **{k: r.v.get(k, "") for k in LIST_COLUMNS}}
        for r in matched[:limit]
    ]
    return {
        "total_matched": len(matched),
        "returned": len(items),
        "truncated": len(matched) > len(items),
        "items": items,
    }


@mcp.tool(description="取得單筆 RFI 的完整內容，含所有欄位、附件清單與修改紀錄。")
async def get_rfi(
    rfi_no: Annotated[str, Field(description="案件編號，如 R26W35-01")],
) -> dict[str, Any]:
    async with SessionLocal() as session:
        rows = await load_rows(session)
        target = next(
            (r for r in rows if (r.obj.rfi_no or "").lower() == rfi_no.strip().lower()),
            None,
        )
        if target is None:
            return {"error": f"找不到編號 {rfi_no}。可先用 search_rfis 找出正確編號。"}

        from .routes.rfis import _get_rfi
        full = await _get_rfi(session, target.obj.id, with_rel=True)
        row = make_rows([full])[0]
        return {
            "rfi_no": full.rfi_no,
            "week": full.week,
            "fields": {f.label: row.v.get(f.key, "") for f in RFI_FIELDS},
            "attachments": [a.filename for a in full.attachments],
            "created_by": full.created_by,
            "updated_by": full.updated_by,
            "updated_at": full.updated_at.isoformat() if full.updated_at else None,
            "revisions": [
                {"version": rev.version, "action": rev.action, "note": rev.note,
                 "edited_by": rev.edited_by,
                 "edited_at": rev.edited_at.isoformat() if rev.edited_at else None,
                 "changes": [
                     {"欄位": c["label"], "原值": c["old"], "新值": c["new"]}
                     for c in (rev.changes or [])
                 ]}
                for rev in full.revisions
            ],
        }


@mcp.tool(
    description=(
        "依維度統計 RFI 筆數，可用於回答「哪一週最多」「BOE 送了幾案」這類問題。"
    )
)
async def get_stats(
    group: Annotated[
        str, Field(description="week / product / vendor / client / status / owner")
    ] = "week",
    year: Annotated[str, Field(description="限定西元年份，如 2026；留空為全部")] = "",
) -> dict[str, Any]:
    if group not in DASH_GROUP_LABELS:
        return {"error": f"不支援的 group：{group}",
                "supported": list(DASH_GROUP_LABELS)}
    async with SessionLocal() as session:
        rows = await load_rows(session)
    result = dashboard_stats(rows, group, year)
    scoped = result["rows"]
    return {
        "group": group,
        "year": year or "全部",
        "total": result["total"],
        "open_cases": sum(1 for r in scoped if r.obj.status not in ("已結案", "暫停")),
        "breakdown": result["table"],
    }


@mcp.tool(
    description=(
        "列出各篩選欄位目前實際存在的值與筆數。"
        "呼叫 search_rfis 前若不確定客戶／面板廠等欄位怎麼寫，先用這支。"
    )
)
async def list_filter_values() -> dict[str, Any]:
    async with SessionLocal() as session:
        rows = await load_rows(session)
    empty = {k: [] for k in FILTER_KEYS}
    options = filter_options(rows, empty, "")
    return {
        key: [{"value": o["value"], "count": o["count"]} for o in vals]
        for key, vals in options.items()
    }


@mcp.tool(description="列出 RFI 的所有欄位定義、型別與可選值，用於理解資料結構。")
async def describe_fields() -> dict[str, Any]:
    return {
        "fields": [
            {"key": f.key, "label": f.label, "type": f.type,
             "required": f.required, "group": f.group,
             "options": f.options or None, "unit": f.unit or None,
             "description": f.info}
            for f in RFI_FIELDS
        ],
        "note": "日期以週別呈現（26W35 = 2026 年第 35 週）；案件編號如 R26W35-01。",
    }


# ── 認證 middleware ───────────────────────────────────────────

class TokenAuthMiddleware:
    """驗證 Bearer token 後才讓請求進到 MCP server。

    這是整個 MCP 的唯一認證接縫：日後若改成完整 OAuth 流程，
    只需要換掉這個 class，上面的工具一行都不用動。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if settings.DEV_AUTH_BYPASS:
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = _bearer(headers.get("authorization"))
        user = None
        if token:
            async with SessionLocal() as session:
                user = await resolve_api_token(session, token)

        if user is None or "read" not in set(user.get("scopes", [])):
            logger.info("MCP 未通過認證的請求：{}", scope.get("path"))
            response = JSONResponse(
                {"error": "unauthorized",
                 "detail": "請帶上 Authorization: Bearer <個人 API Token>，"
                           "可在平台的「API Token」頁面建立。"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="sa-rfi"'},
            )
            await response(scope, receive, send)
            return

        # 讓工具端（若需要）能知道是誰在呼叫
        scope.setdefault("state", {})["mcp_user"] = user
        await self.app(scope, receive, send)


def build_mcp_app() -> ASGIApp:
    """組出可掛載的 MCP ASGI app（stateless，適合多副本部署）。"""
    inner = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,   # 不保留 session，任何副本都能處理任何請求
        json_response=True,    # 單純的 JSON 回應，不走 SSE，對反向代理較友善
    )
    return TokenAuthMiddleware(inner)
