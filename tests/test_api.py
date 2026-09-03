"""唯讀 JSON API 的回應形狀。

API 與網頁共用同一套篩選規則，這裡確認兩者結果一致 ——
「畫面所見即 API 所得」是這個設計的重點。
"""

import pytest

from tests.conftest import create_rfi


class TestListEndpoint:
    async def test_shape(self, client, rfi_form):
        await create_rfi(client, rfi_form())
        body = (await client.get("/api/v1/rfis")).json()
        assert set(body) == {"total", "limit", "offset", "filters", "items"}
        item = body["items"][0]
        assert set(item) >= {"id", "rfi_no", "week", "values", "display",
                             "attachments", "version"}

    async def test_values_are_raw_display_is_formatted(self, client, rfi_form):
        """values 給程式運算，display 與網頁／投影片上看到的一致。"""
        await create_rfi(client, rfi_form(size="14.0", refresh_rate="60"))
        item = (await client.get("/api/v1/rfis")).json()["items"][0]
        assert item["values"]["size"] == "14.0"
        assert item["display"]["size"] == '14.0"'
        assert item["display"]["refresh_rate"] == "60Hz"

    async def test_pagination(self, client, rfi_form):
        for i in range(5):
            await create_rfi(client, rfi_form(code=f"C{i}"))
        body = (await client.get("/api/v1/rfis?limit=2&offset=1")).json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["offset"] == 1

    @pytest.mark.parametrize(("query", "status"), [
        ("limit=0", 422), ("limit=9999", 422), ("offset=-1", 422), ("dir=sideways", 422),
    ])
    async def test_invalid_params_rejected(self, client, query, status):
        assert (await client.get(f"/api/v1/rfis?{query}")).status_code == status

    async def test_filters_match_web_ui(self, client, rfi_form, session):
        """同一組條件，API 與網頁列表必須挑出同一批資料。"""
        await create_rfi(client, rfi_form(client="HP", vendor="BOE"))
        await create_rfi(client, rfi_form(client="Dell", vendor="AUO"))

        api = (await client.get("/api/v1/rfis?vendor=BOE")).json()
        html = (await client.get("/rfis?vendor=BOE",
                                 headers={"Accept": "text/html"})).text
        assert api["total"] == 1
        assert api["items"][0]["display"]["client"] == "HP"
        assert "Dell" not in html.split("<tbody>")[1].split("</tbody>")[0]

    async def test_multi_value_filter(self, client, rfi_form):
        await create_rfi(client, rfi_form(vendor="BOE"))
        await create_rfi(client, rfi_form(vendor="AUO", code="X"))
        body = (await client.get("/api/v1/rfis?vendor=BOE&vendor=AUO")).json()
        assert body["total"] == 2


class TestDetailEndpoint:
    async def test_includes_revisions(self, client, rfi_form):
        loc = await create_rfi(client, rfi_form(), note="建檔")
        rfi_id = int(loc.rsplit("/", 1)[1])
        body = (await client.get(f"/api/v1/rfis/{rfi_id}")).json()
        assert body["rfi_no"].startswith("R26W")
        assert len(body["revisions"]) == 1
        assert body["revisions"][0]["note"] == "建檔"

    async def test_missing_is_404(self, client):
        assert (await client.get("/api/v1/rfis/99999")).status_code == 404


class TestOtherEndpoints:
    async def test_fields(self, client):
        body = (await client.get("/api/v1/fields")).json()
        assert body["fields"]
        assert "list_columns" in body and "filter_keys" in body

    async def test_filters_options(self, client, rfi_form):
        await create_rfi(client, rfi_form(vendor="BOE"))
        body = (await client.get("/api/v1/filters")).json()
        assert "BOE" in {o["value"] for o in body["options"]["vendor"]}

    async def test_stats(self, client, rfi_form):
        await create_rfi(client, rfi_form(status="評估中"))
        await create_rfi(client, rfi_form(status="已結案", code="X"))
        body = (await client.get("/api/v1/stats?group=status")).json()
        assert body["total"] == 2
        assert body["open_cases"] == 1        # 已結案不算未結案
        assert {b["category"] for b in body["breakdown"]} == {"評估中", "已結案"}

    async def test_stats_rejects_bad_group(self, client):
        assert (await client.get("/api/v1/stats?group=bogus")).status_code == 400

    async def test_no_write_endpoints_exist(self, client):
        """API 是刻意唯讀的 —— 不該有任何 POST/PUT/DELETE 路由。"""
        spec = (await client.get("/openapi.json")).json()
        for path, ops in spec["paths"].items():
            if path.startswith("/api/"):
                assert set(ops) <= {"get"}, f"{path} 有非 GET 的方法：{set(ops)}"

