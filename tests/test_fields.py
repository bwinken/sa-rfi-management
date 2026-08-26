"""欄位定義與值處理 —— app/fields.py。

這裡是整個平台的單一事實來源，顯示字串的規則同時影響網頁、Excel、
投影片與 diff，所以值得逐條釘住。
"""

from datetime import date

import pytest

from app.fields import (
    FIELD_BY_KEY,
    FILTER_KEYS,
    LIST_COLUMNS,
    RFI_FIELDS,
    SLIDE_COLUMNS,
    clean_payload,
    diff_payload,
    display,
    is_week_label,
    short_week,
    to_iso_date,
    week_label,
    week_to_date,
)


class TestWeekLabel:
    """SA 用週別（26W35）而非日期在溝通，換算錯了整份週報就錯。"""

    @pytest.mark.parametrize(("iso", "expected"), [
        ("2026-08-24", "26W35"),
        ("2026-01-01", "26W01"),   # 年初
        ("2026-12-31", "26W53"),   # 2026 是 53 週年，12/31（週四）仍屬 W53
        ("2024-12-30", "25W01"),   # 跨年：12/30（週一）已屬下一年的 W01
    ])
    def test_iso_date_to_week(self, iso, expected):
        assert week_label(iso) == expected

    def test_accepts_date_object(self):
        assert week_label(date(2026, 8, 24)) == "26W35"

    def test_already_week_label_passes_through(self):
        assert week_label("26W35") == "26W35"
        assert week_label("26w35") == "26W35"

    def test_empty_input(self):
        assert week_label("") == ""
        assert week_label(None) == ""

    def test_round_trip(self):
        """週別換回日期後，再換成週別必須一致。"""
        for label in ("26W01", "26W35", "26W52"):
            back = week_to_date(label)
            assert week_label(back) == label

    def test_week_to_date_returns_monday(self):
        assert date.fromisoformat(week_to_date("26W35")).weekday() == 0

    @pytest.mark.parametrize("bad", ["", "26W", "2026W35", "abc", "26X35"])
    def test_is_week_label_rejects_malformed(self, bad):
        assert is_week_label(bad) is False

    def test_short_week(self):
        assert short_week("26W35") == "W35"
        assert short_week("") == ""


class TestToIsoDate:
    """匯入時來源格式很雜，全部要正規化成 ISO。"""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("2026-08-24", "2026-08-24"),
        ("2026/08/24", "2026-08-24"),
        ("2026.08.24", "2026-08-24"),
        (date(2026, 8, 24), "2026-08-24"),
        ("26W35", "2026-08-24"),      # 週別 → 該週星期一
        ("", ""),
        (None, ""),
        ("not a date", ""),
    ])
    def test_normalizes(self, raw, expected):
        assert to_iso_date(raw) == expected


class TestDisplay:
    """顯示字串會直接進 Excel 與投影片，格式必須穩定。"""

    def test_unit_is_appended(self):
        assert display(FIELD_BY_KEY["size"], {"size": "14.0"}) == '14.0"'
        assert display(FIELD_BY_KEY["refresh_rate"], {"refresh_rate": "60"}) == "60Hz"

    def test_unit_not_doubled(self):
        """使用者若自己打了單位，不應該變成 14.0""。"""
        assert display(FIELD_BY_KEY["size"], {"size": '14.0"'}) == '14.0"'

    def test_resolution_composite(self):
        f = FIELD_BY_KEY["resolution"]
        assert display(f, {"resolution": {"w": "2560", "h": "1600"}}) == "2560 x 1600"

    def test_resolution_partial(self):
        f = FIELD_BY_KEY["resolution"]
        assert display(f, {"resolution": {"w": "2560", "h": ""}}) == "2560 x ?"

    def test_resolution_empty(self):
        f = FIELD_BY_KEY["resolution"]
        assert display(f, {"resolution": {"w": "", "h": ""}}) == ""

    def test_missing_key(self):
        assert display(FIELD_BY_KEY["notes"], {}) == ""


class TestCleanPayload:
    def test_strips_whitespace(self):
        data = clean_payload({"client": "  HP  ", "vendor": "BOE"})
        assert data["client"] == "HP"

    def test_composite_field_collected(self):
        data = clean_payload({"resolution_w": "2560", "resolution_h": "1600"})
        assert data["resolution"] == {"w": "2560", "h": "1600"}

    def test_unknown_keys_dropped(self):
        data = clean_payload({"client": "HP", "evil": "x"})
        assert "evil" not in data

    def test_all_fields_present(self):
        """即使表單什麼都沒送，每個欄位都要有鍵，diff 才不會誤判。"""
        data = clean_payload({})
        assert set(data) == {f.key for f in RFI_FIELDS}


class TestDiffPayload:
    def test_detects_change(self):
        old = clean_payload({"client": "HP"})
        new = clean_payload({"client": "Dell"})
        changes = diff_payload(old, new)
        assert [c["key"] for c in changes] == ["client"]
        assert changes[0]["old"] == "HP"
        assert changes[0]["new"] == "Dell"

    def test_no_change_is_empty(self):
        data = clean_payload({"client": "HP", "vendor": "BOE"})
        assert diff_payload(data, data) == []

    def test_compares_by_display_not_raw(self):
        """14.0 與 14.0" 顯示相同，不該被當成修改。"""
        old = clean_payload({"size": "14.0"})
        new = clean_payload({"size": '14.0"'})
        assert diff_payload(old, new) == []

    def test_composite_change(self):
        old = clean_payload({"resolution_w": "2560", "resolution_h": "1600"})
        new = clean_payload({"resolution_w": "2880", "resolution_h": "1800"})
        changes = diff_payload(old, new)
        assert changes[0]["old"] == "2560 x 1600"
        assert changes[0]["new"] == "2880 x 1800"


class TestFieldDefinitions:
    """設定檔式的定義容易手滑，這些檢查很便宜但擋得住低級錯誤。"""

    def test_keys_unique(self):
        keys = [f.key for f in RFI_FIELDS]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("key", LIST_COLUMNS + SLIDE_COLUMNS)
    def test_referenced_columns_exist(self, key):
        assert key in FIELD_BY_KEY

    def test_filter_keys_exist(self):
        """week 是 rfi_date 衍生的虛擬欄位，其餘都必須是真欄位。"""
        for key in FILTER_KEYS:
            assert key == "week" or key in FIELD_BY_KEY

    def test_select_fields_have_options(self):
        for f in RFI_FIELDS:
            if f.type == "select":
                assert f.options, f"{f.key} 是 select 卻沒有選項"

    def test_every_field_has_label_and_group(self):
        for f in RFI_FIELDS:
            assert f.label and f.group
