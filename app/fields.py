"""RFI 欄位定義（單一事實來源）。

所有表單、列表、篩選、Dashboard、Excel 匯入/匯出、投影片匯出與修改紀錄 diff
都從這裡迭代欄位；新增／調整欄位只需修改本檔，其餘畫面自動套用。
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

FieldType = Literal[
    "select", "combo", "text", "textarea", "number", "date", "resolution",
]


@dataclass(frozen=True)
class RfiField:
    key: str          # 資料庫 / JSON 中的鍵名（英文）
    label: str        # UI 顯示名稱
    type: FieldType
    options: list[str] = field(default_factory=list)  # select / combo 的建議值
    placeholder: str = ""
    required: bool = False
    group: str = "其他"   # 表單分區
    info: str = ""        # 欄位說明（tooltip）
    unit: str = ""        # 顯示時附加的單位（如 Hz、"）
    short: str = ""       # 表格 / 投影片用的短標題（留空則用 label）
    # 複合欄位的子欄位：(子鍵後綴, 子標籤, 單位)。resolution → 寬 W / 高 H
    subfields: tuple = ()

    @property
    def head(self) -> str:
        """表格標題（短標題優先）。"""
        return self.short or self.label


# 欄位順序即為表單、列表與投影片的呈現順序
RFI_FIELDS: list[RfiField] = [
    # ── 案件基本資訊 ──
    RfiField("rfi_date", "RFI 日期", "date", required=True, group="案件基本資訊",
             short="日期",
             info="客戶提出 RFI 的日期；系統會自動換算成週別（如 26W29）供列表與投影片使用。"),
    RfiField("client", "終端客戶", "combo",
             ["HP", "Dell", "Lenovo", "Asus", "Acer", "Apple", "Samsung", "MSI"],
             required=True, group="案件基本資訊",
             info="終端品牌客戶。清單為常用值，可直接輸入清單以外的客戶。"),
    RfiField("vendor", "面板廠", "combo",
             ["BOE", "AUO", "Innolux", "CSOT", "Tianma", "Visionox", "SDC", "LGD", "EDO"],
             required=True, group="案件基本資訊",
             info="提出需求的面板廠。清單為常用值，可自由輸入。"),
    RfiField("product", "產品類別", "select",
             ["OLED NB", "OLED Monitor", "手機", "平板", "相機", "穿戴", "掌機", "其他"],
             required=True, group="案件基本資訊", short="產品",
             info="專案的產品應用類別，Dashboard 會依此統計。"),
    RfiField("code", "專案代號", "text", placeholder="例：Pro-14",
             group="案件基本資訊", short="代號",
             info="客戶或內部的專案代號／機種代號。例：Pro-14"),

    # ── 面板 / IC 規格 ──
    RfiField("ic", "IC 型號", "combo", placeholder="例：NT3670B",
             group="面板 / IC 規格", short="IC型號",
             info="評估中的驅動 IC 型號。可自由輸入，清單會累積既有型號。"),
    RfiField("size", "面板尺寸", "number", placeholder="例：14.0", unit='"',
             group="面板 / IC 規格", short="Size",
             info='面板對角尺寸，單位吋。只填數字即可，顯示時自動加上 "。例：14.0'),
    RfiField("resolution", "解析度", "resolution",
             subfields=(("w", "寬 W", "px"), ("h", "高 H", "px")),
             group="面板 / IC 規格", short="Resolution",
             info="面板解析度，寬 × 高（px）。例：2560 x 1600"),
    RfiField("refresh_rate", "更新頻率", "number", placeholder="例：60", unit="Hz",
             group="面板 / IC 規格", short="Hz",
             info="面板更新率，單位 Hz。只填數字即可。例：60、120"),
    RfiField("mux", "Mux", "select", ["None", "Mux1", "Mux2", "eMux", "Mux1 and 2"],
             group="面板 / IC 規格",
             info="多工（Mux）架構；未使用請選 None。"),
    RfiField("panel_type", "封裝 / 材質", "select",
             ["COF，柔", "COF，硬", "COP，柔", "COP，硬"],
             group="面板 / IC 規格", short="Type",
             info="封裝方式（COF / COP）與基板軟硬（柔性 / 硬性）。"),
    RfiField("special", "特殊規格", "text", placeholder="例：HDR400、Touch in Cell",
             group="面板 / IC 規格", short="特殊",
             info="此案的特殊需求或加值規格。例：HDR400、LTPO、屏下攝像"),

    # ── 案件追蹤 ──
    RfiField("status", "處理狀態", "select",
             ["評估中", "已回覆客戶", "追蹤中", "已結案", "暫停"],
             required=True, group="案件追蹤", short="狀態",
             info="本筆 RFI 目前的處理狀態，Dashboard 與列表皆可依此篩選。"),
    RfiField("owner", "負責 SA", "combo", placeholder="例：Alex Lin",
             group="案件追蹤", short="負責SA",
             info="本案的負責 SA／窗口，方便追蹤未結案的案件。"),
    RfiField("contact", "客戶窗口", "text", placeholder="例：Alex Lin",
             group="案件追蹤", short="客戶窗口",
             info="客戶端的對接窗口姓名。"),
    RfiField("notes", "評估事項", "textarea", placeholder="請輸入詳細評估事項…",
             group="案件追蹤", short="評估事項",
             info="需要評估／確認的技術項目，會完整帶入匯出的投影片。"),
    RfiField("risk", "風險評估", "textarea", placeholder="例：無／IC 產能吃緊",
             group="案件追蹤", short="風險",
             info="目前已知的風險或卡關點；無風險請填「無」。"),
]

FIELD_KEYS = [f.key for f in RFI_FIELDS]
FIELD_BY_KEY = {f.key: f for f in RFI_FIELDS}

# 列表表格的欄位順序（週別欄由 rfi_date 衍生，單獨處理）
LIST_COLUMNS = [
    "client", "vendor", "ic", "size", "resolution", "refresh_rate", "mux",
    "special", "panel_type", "product", "code", "status", "owner",
    "notes", "risk", "contact",
]

# 匯出投影片時呈現的欄位（沿用 SA 原本週報的欄序）
SLIDE_COLUMNS = [
    "client", "vendor", "ic", "size", "resolution", "refresh_rate", "mux",
    "special", "panel_type", "product", "code", "notes",
]

# 多選篩選器涵蓋的欄位（"week" 為 rfi_date 衍生的虛擬欄位）
FILTER_KEYS = ["week", "product", "client", "vendor", "ic", "size", "status"]

FILTER_LABELS = {
    "week": "週別",
    "product": "產品",
    "client": "終端客戶",
    "vendor": "面板廠",
    "ic": "IC型號",
    "size": "尺寸",
    "status": "狀態",
}

# 附件允許的副檔名
ALLOWED_ATTACHMENT_EXT = {".pdf", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg"}


def grouped_fields() -> list[tuple]:
    """依 group 分組（保留欄位原始順序），回傳 [(group_name, [fields]), ...]。"""
    groups: list[tuple] = []
    index: dict = {}
    for f in RFI_FIELDS:
        if f.group not in index:
            index[f.group] = []
            groups.append((f.group, index[f.group]))
        index[f.group].append(f)
    return groups


# ── 週別（ISO week）換算 ──────────────────────────────────────

def week_label(value) -> str:
    """把日期換算成 SA 習慣的週別標記，如 2026-07-13 → 「26W29」。

    接受 date / datetime / "YYYY-MM-DD" 字串；已是週別格式則原樣回傳。
    """
    if not value:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if is_week_label(text):
            return text.upper()
        try:
            value = date.fromisoformat(text[:10])
        except ValueError:
            return text
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return str(value)
    iso_year, iso_week, _ = value.isocalendar()
    return f"{iso_year % 100:02d}W{iso_week:02d}"


def is_week_label(text: str) -> bool:
    """字串是否為週別格式（如 26W29）。"""
    text = (text or "").strip().upper()
    return (
        len(text) == 5 and text[2] == "W"
        and text[:2].isdigit() and text[3:].isdigit()
    )


def week_to_date(text: str) -> str:
    """把週別（26W29）換算回該週星期一的日期字串；失敗回傳空字串。"""
    text = (text or "").strip().upper()
    if not is_week_label(text):
        return ""
    try:
        return date.fromisocalendar(2000 + int(text[:2]), int(text[3:]), 1).isoformat()
    except ValueError:
        return ""


def to_iso_date(value) -> str:
    """把各種來源（Excel 日期、字串、週別）正規化成 YYYY-MM-DD；失敗回傳空字串。"""
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if is_week_label(text):
        return week_to_date(text)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def short_week(label: str) -> str:
    """把 26W29 縮寫成 W29（投影片標題用）。"""
    return f"W{label.split('W')[1]}" if "W" in (label or "") else (label or "")


# ── 表單資料處理 ──────────────────────────────────────────────

def clean_payload(raw: dict) -> dict:
    """從表單原始資料萃取出已定義欄位、去除前後空白。

    resolution 這類複合欄位由 <key>_<sub> 兩個輸入組成，存成 {"w": .., "h": ..}。
    """
    data = {}
    for f in RFI_FIELDS:
        if f.subfields:
            data[f.key] = {
                sub: (raw.get(f"{f.key}_{sub}", "") or "").strip()
                for sub, *_ in f.subfields
            }
        else:
            val = raw.get(f.key, "")
            data[f.key] = (val or "").strip() if isinstance(val, str) else (val or "")
    return data


def display(f: RfiField, data: dict) -> str:
    """將某欄位的值轉成可顯示字串。

    resolution → 「2560 x 1600」；帶 unit 的欄位自動附上單位（14.0" / 60Hz）。
    """
    val = (data or {}).get(f.key)

    if f.type == "resolution":
        d = val if isinstance(val, dict) else {}
        parts = [d.get(sub, "") for sub, *_ in f.subfields]
        return " x ".join(p or "?" for p in parts) if any(parts) else ""

    if val in (None, ""):
        return ""
    text = str(val).strip()
    if f.unit and not text.endswith(f.unit):
        text += f.unit
    return text


def display_all(data: dict) -> dict:
    """一次算好整筆資料所有欄位的顯示字串（列表、匯出共用）。"""
    return {f.key: display(f, data) for f in RFI_FIELDS}


def diff_payload(old: dict, new: dict) -> list[dict]:
    """比較兩份資料，回傳有變動的欄位清單（以顯示字串比較），供修改紀錄顯示。"""
    changes = []
    for f in RFI_FIELDS:
        o = display(f, old)
        n = display(f, new)
        if o != n:
            changes.append({"key": f.key, "label": f.label, "old": o, "new": n})
    return changes
