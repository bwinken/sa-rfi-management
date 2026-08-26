"""列表查詢共用邏輯：多選篩選、關鍵字、排序、交叉篩選選項。

RFI 的資料量級（數百～數千筆）遠小於一次查詢的成本，因此一律先讀出全部
符合權限的資料再於 Python 端處理。好處是「交叉篩選」（某欄位的可選值會
隨其他欄位的選取而縮減）與列表 / 匯出 / 投影片能共用完全相同的一套規則。
"""

import re
from dataclasses import dataclass
from urllib.parse import urlencode

from .fields import FILTER_KEYS, LIST_COLUMNS, display_all
from .models import Rfi

# 可排序的欄位（週別與編號為衍生欄位，其餘來自 LIST_COLUMNS）
SORTABLE = ["week", "rfi_no", *LIST_COLUMNS]


@dataclass
class RfiRow:
    """一筆 RFI 與它算好的顯示字串（避免模板重複計算）。"""

    obj: Rfi
    v: dict

    @property
    def id(self) -> int:
        return self.obj.id

    @property
    def search_text(self) -> str:
        return self._search

    def __post_init__(self) -> None:
        self._search = " ".join(
            [self.obj.rfi_no or "", *(x for x in self.v.values() if x)]
        ).lower()


def make_rows(rfis: list[Rfi]) -> list[RfiRow]:
    rows = []
    for r in rfis:
        values = display_all(r.data or {})
        values["week"] = r.week or ""
        values["rfi_no"] = r.rfi_no or ""
        rows.append(RfiRow(obj=r, v=values))
    return rows


def parse_filters(params) -> dict[str, list[str]]:
    """從 query string 取出各篩選欄位的勾選值（可重複參數）。"""
    return {k: [v for v in params.getlist(k) if v] for k in FILTER_KEYS}


def filter_rows(rows: list[RfiRow], filters: dict, q: str = "") -> list[RfiRow]:
    term = (q or "").strip().lower()
    out = []
    for row in rows:
        if term and term not in row.search_text:
            continue
        if all(not sel or row.v.get(key, "") in sel for key, sel in filters.items()):
            out.append(row)
    return out


def filter_options(rows: list[RfiRow], filters: dict, q: str = "") -> dict[str, list[dict]]:
    """各篩選欄位的可選值 — 交叉篩選：排除「自己」以外的所有條件後統計。

    例：已勾選 client=HP 時，「面板廠」下拉只會列出 HP 案件出現過的面板廠，
    但「終端客戶」下拉仍列出全部客戶（否則勾了就再也改不掉）。
    """
    options: dict[str, list[dict]] = {}
    for key in FILTER_KEYS:
        others = {k: v for k, v in filters.items() if k != key}
        subset = filter_rows(rows, others, q)
        counts: dict[str, int] = {}
        for row in subset:
            val = row.v.get(key, "")
            if val:
                counts[val] = counts.get(val, 0) + 1
        # 已勾選但在目前條件下沒有資料的值仍要列出，否則使用者無法取消勾選
        for val in filters.get(key, []):
            counts.setdefault(val, 0)
        options[key] = [
            {"value": val, "count": counts[val], "checked": val in filters.get(key, [])}
            for val in sorted(counts, key=natural_key)
        ]
    return options


def natural_key(text: str) -> list:
    """自然排序鍵：讓 "9.7"" 排在 "14.0"" 前、"W9" 排在 "W10" 前。"""
    parts = []
    for chunk in re.split(r"(\d+(?:\.\d+)?)", (text or "").strip()):
        if not chunk:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", chunk):
            parts.append((0, float(chunk), ""))
        else:
            parts.append((1, 0.0, chunk.lower()))
    return parts


def sort_rows(rows: list[RfiRow], sort: str, direction: str) -> list[RfiRow]:
    """依欄位排序；未指定或欄位不合法時，回到「日期新→舊」的預設。"""
    reverse = direction != "asc"
    if sort not in SORTABLE:
        return sorted(
            rows,
            key=lambda r: (r.obj.rfi_date is not None, r.obj.rfi_date, r.obj.id),
            reverse=True,
        )
    # 空值一律排在最後 —— 不論升冪或降冪。
    # 不能只靠 sorted(reverse=) 加一個「是否為空」的排序鍵：那個鍵也會被
    # 一起反轉，降冪時空值反而跑到最前面。所以拆成兩堆各自排序再接起來。
    filled = [r for r in rows if r.v.get(sort)]
    blank = [r for r in rows if not r.v.get(sort)]
    filled.sort(key=lambda r: (natural_key(r.v.get(sort, "")), r.obj.id), reverse=reverse)
    blank.sort(key=lambda r: r.obj.id)
    return filled + blank


def query_string(filters: dict, q: str = "", sort: str = "", direction: str = "",
                 **extra) -> str:
    """把目前的篩選條件重組成 query string，供匯出連結沿用同一份條件。"""
    pairs: list[tuple[str, str]] = []
    if q:
        pairs.append(("q", q))
    for key in FILTER_KEYS:
        for val in filters.get(key, []):
            pairs.append((key, val))
    if sort:
        pairs.append(("sort", sort))
        pairs.append(("dir", direction or "desc"))
    for key, val in extra.items():
        if val:
            pairs.append((key, str(val)))
    return urlencode(pairs)


def active_filter_count(filters: dict, q: str = "") -> int:
    return sum(len(v) for v in filters.values()) + (1 if q else 0)
