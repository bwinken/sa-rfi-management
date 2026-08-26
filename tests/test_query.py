"""列表查詢邏輯 —— app/query.py。

這一層被網頁列表、JSON API 與匯出共用，行為不一致的話
「畫面所見即匯出內容」這個承諾就破了。
"""

import pytest

from app.fields import clean_payload
from app.models import Rfi
from app.query import (
    active_filter_count,
    filter_options,
    filter_rows,
    make_rows,
    natural_key,
    query_string,
    sort_rows,
)
from app.routes.rfis import sync_columns


def _rfi(**overrides) -> Rfi:
    raw = {
        "rfi_date": "2026-08-24", "client": "HP", "vendor": "BOE",
        "product": "OLED NB", "ic": "NT3670B", "size": "14.0",
        "status": "評估中", "mux": "None",
    }
    raw.update(overrides)
    rfi = Rfi()
    sync_columns(rfi, clean_payload(raw))
    rfi.id = overrides.pop("_id", 1)
    rfi.rfi_no = f"R{rfi.week}-01"
    rfi.attachments = []
    return rfi


@pytest.fixture
def rows():
    data = [
        _rfi(_id=1, client="HP", vendor="BOE", size="14.0", product="OLED NB"),
        _rfi(_id=2, client="Dell", vendor="AUO", size="27.0", product="OLED Monitor"),
        _rfi(_id=3, client="Asus", vendor="BOE", size="9.7", product="平板",
             rfi_date="2026-08-17"),
        _rfi(_id=4, client="HP", vendor="CSOT", size="", product="手機",
             rfi_date="2026-08-10"),
    ]
    return make_rows(data)


class TestNaturalSort:
    """尺寸是字串，字典序會把 9.7" 排在 14.0" 後面，對 SA 是錯的。"""

    def test_numbers_sort_numerically(self):
        values = ["14.0", "9.7", "27.0", "6.1"]
        assert sorted(values, key=natural_key) == ["6.1", "9.7", "14.0", "27.0"]

    def test_week_labels_sort_correctly(self):
        weeks = ["26W9", "26W10", "26W2"]
        assert sorted(weeks, key=natural_key) == ["26W2", "26W9", "26W10"]

    def test_mixed_text_and_number(self):
        assert natural_key("Mux2") < natural_key("Mux10")

    def test_empty_string(self):
        assert natural_key("") == []


class TestSortRows:
    def test_sort_by_size_ascending(self, rows):
        out = sort_rows(rows, "size", "asc")
        sizes = [r.v["size"] for r in out if r.v["size"]]
        assert sizes == ['9.7"', '14.0"', '27.0"']

    def test_empty_values_always_last(self, rows):
        """不論升冪降冪，未填的都排最後，才不會干擾閱讀。"""
        for direction in ("asc", "desc"):
            out = sort_rows(rows, "size", direction)
            assert out[-1].v["size"] == ""

    def test_unknown_column_falls_back_to_date_desc(self, rows):
        out = sort_rows(rows, "not_a_column", "asc")
        assert out[0].obj.rfi_date >= out[-1].obj.rfi_date


class TestFilterRows:
    def test_single_value(self, rows):
        out = filter_rows(rows, {"vendor": ["BOE"]})
        assert {r.obj.client for r in out} == {"HP", "Asus"}

    def test_multiple_values_are_or(self, rows):
        out = filter_rows(rows, {"vendor": ["BOE", "AUO"]})
        assert len(out) == 3

    def test_different_fields_are_and(self, rows):
        out = filter_rows(rows, {"vendor": ["BOE"], "client": ["HP"]})
        assert len(out) == 1

    def test_empty_filter_matches_all(self, rows):
        assert len(filter_rows(rows, {"vendor": []})) == len(rows)

    def test_keyword_searches_all_fields(self, rows):
        assert len(filter_rows(rows, {}, "NT3670B")) == len(rows)
        assert len(filter_rows(rows, {}, "Dell")) == 1

    def test_keyword_is_case_insensitive(self, rows):
        assert len(filter_rows(rows, {}, "dell")) == 1

    def test_keyword_and_filter_combine(self, rows):
        assert len(filter_rows(rows, {"vendor": ["BOE"]}, "Asus")) == 1


class TestFilterOptions:
    """交叉篩選：選項要隨其他條件收斂，但自己那一欄要保持完整。"""

    def test_options_narrow_by_other_filters(self, rows):
        opts = filter_options(rows, {"client": ["HP"], "vendor": [], "week": [],
                                     "product": [], "status": [], "ic": [], "size": []})
        vendors = {o["value"] for o in opts["vendor"]}
        assert vendors == {"BOE", "CSOT"}     # 只剩 HP 出現過的面板廠

    def test_own_field_stays_complete(self, rows):
        """已勾 client=HP 時，client 下拉仍要列出全部客戶，否則改不掉。"""
        opts = filter_options(rows, {"client": ["HP"], "vendor": [], "week": [],
                                     "product": [], "status": [], "ic": [], "size": []})
        clients = {o["value"] for o in opts["client"]}
        assert clients == {"HP", "Dell", "Asus"}

    def test_counts_are_correct(self, rows):
        empty = dict.fromkeys(
            ("week", "product", "client", "vendor", "ic", "size", "status"), []
        )
        opts = filter_options(rows, empty)
        by_value = {o["value"]: o["count"] for o in opts["vendor"]}
        assert by_value["BOE"] == 2

    def test_checked_flag(self, rows):
        empty = dict.fromkeys(
            ("week", "product", "client", "vendor", "ic", "size", "status"), []
        )
        opts = filter_options(rows, {**empty, "vendor": ["BOE"]})
        checked = {o["value"] for o in opts["vendor"] if o["checked"]}
        assert checked == {"BOE"}

    def test_selected_value_listed_even_with_zero_matches(self, rows):
        """否則使用者會被卡住：勾了一個現在沒資料的值，就再也取消不了。"""
        empty = dict.fromkeys(
            ("week", "product", "client", "vendor", "ic", "size", "status"), []
        )
        opts = filter_options(rows, {**empty, "vendor": ["BOE"], "client": ["Dell"]})
        assert "BOE" in {o["value"] for o in opts["vendor"]}


class TestQueryString:
    def test_round_trips_filters(self):
        qs = query_string({"vendor": ["BOE", "AUO"], "client": []}, "NT")
        assert "vendor=BOE" in qs and "vendor=AUO" in qs and "q=NT" in qs

    def test_omits_empty(self):
        assert query_string({"vendor": []}, "") == ""

    def test_includes_sort(self):
        qs = query_string({}, "", "size", "asc")
        assert "sort=size" in qs and "dir=asc" in qs

    def test_active_filter_count(self):
        assert active_filter_count({"vendor": ["BOE"], "client": ["HP", "Dell"]}, "x") == 4
        assert active_filter_count({"vendor": []}, "") == 0
