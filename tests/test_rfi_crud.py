"""RFI 建立、編輯與並行合併。

欄位層級合併是整個平台最不直觀的一段：送出的是完整表單，但只有
「相對於開啟編輯頁時的 baseline 有變動」的欄位才會被套用。
沒有測試釘住的話，很容易在重構時退化成「整份覆蓋」。
"""

import json

import pytest
from sqlalchemy import select

from app.models import Rfi, RfiRevision
from tests.conftest import create_rfi


async def _baseline(session, rfi_id: int) -> str:
    rfi = (await session.execute(select(Rfi).where(Rfi.id == rfi_id))).scalar_one()
    return json.dumps(rfi.data)


class TestCreate:
    async def test_creates_and_redirects(self, client, rfi_form):
        loc = await create_rfi(client, rfi_form())
        assert loc.startswith("/rfis/")

    async def test_derives_week_and_number(self, client, rfi_form, session):
        await create_rfi(client, rfi_form(rfi_date="2026-08-24"))
        rfi = (await session.execute(select(Rfi))).scalar_one()
        assert rfi.week == "26W35"
        assert rfi.rfi_no == "R26W35-01"

    async def test_numbers_increment_within_week(self, client, rfi_form, session):
        await create_rfi(client, rfi_form(client="HP"))
        await create_rfi(client, rfi_form(client="Dell"))
        nos = sorted(
            (await session.execute(select(Rfi.rfi_no))).scalars().all()
        )
        assert nos == ["R26W35-01", "R26W35-02"]

    async def test_records_creation_revision(self, client, rfi_form, session):
        await create_rfi(client, rfi_form(), note="初次建檔")
        rev = (await session.execute(select(RfiRevision))).scalar_one()
        assert rev.action == "create"
        assert rev.note == "初次建檔"
        assert rev.changes

    @pytest.mark.parametrize("missing", ["rfi_date", "client", "vendor", "product", "status"])
    async def test_required_fields_rejected(self, client, rfi_form, missing):
        form = rfi_form()
        form[missing] = ""
        resp = await client.post("/rfis/new", data=form)
        assert resp.status_code == 400
        assert "必填" in resp.text

    async def test_bad_date_rejected(self, client, rfi_form):
        resp = await client.post("/rfis/new", data=rfi_form(rfi_date="not-a-date"))
        assert resp.status_code == 400


class TestEdit:
    async def test_updates_and_records_diff(self, client, rfi_form, session):
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        base = await _baseline(session, rfi_id)

        resp = await client.post(
            f"/rfis/{rfi_id}/edit",
            data={**rfi_form(status="已回覆客戶"), "baseline": base, "note": "已回覆"},
        )
        assert resp.status_code == 303

        revs = (await session.execute(
            select(RfiRevision).where(RfiRevision.action == "update")
        )).scalars().all()
        assert len(revs) == 1
        assert revs[0].note == "已回覆"
        changed = {c["key"] for c in revs[0].changes}
        assert changed == {"status"}

    async def test_note_required_when_fields_change(self, client, rfi_form, session):
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        base = await _baseline(session, rfi_id)
        resp = await client.post(
            f"/rfis/{rfi_id}/edit",
            data={**rfi_form(status="已結案"), "baseline": base},   # 沒有 note
        )
        assert resp.status_code == 400
        assert "修改說明" in resp.text

    async def test_no_change_is_a_noop(self, client, rfi_form, session):
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        base = await _baseline(session, rfi_id)
        resp = await client.post(
            f"/rfis/{rfi_id}/edit", data={**rfi_form(), "baseline": base},
        )
        assert resp.status_code == 303
        updates = (await session.execute(
            select(RfiRevision).where(RfiRevision.action == "update")
        )).scalars().all()
        assert updates == []

    async def test_version_increments(self, client, rfi_form, session):
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        base = await _baseline(session, rfi_id)
        await client.post(f"/rfis/{rfi_id}/edit",
                          data={**rfi_form(risk="有風險"), "baseline": base, "note": "更新"})
        rfi = (await session.execute(select(Rfi).where(Rfi.id == rfi_id))).scalar_one()
        assert rfi.version == 2


class TestConcurrentMerge:
    """兩個人同時編輯的行為，是這個平台相對於原雛形最重要的差異。"""

    async def test_different_fields_both_survive(self, client, rfi_form, session):
        """A 改狀態、B 改風險，兩邊都要保留 —— 不是後蓋前。"""
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        shared_base = await _baseline(session, rfi_id)

        # A 送出：只改 status
        r1 = await client.post(
            f"/rfis/{rfi_id}/edit",
            data={**rfi_form(status="已回覆客戶"), "baseline": shared_base, "note": "A"},
        )
        assert r1.status_code == 303

        # B 用同一份（已過期的）baseline 送出：只改 risk
        r2 = await client.post(
            f"/rfis/{rfi_id}/edit",
            data={**rfi_form(risk="頻寬不足"), "baseline": shared_base, "note": "B"},
        )
        assert r2.status_code == 303

        rfi = (await session.execute(select(Rfi).where(Rfi.id == rfi_id))).scalar_one()
        assert rfi.data["status"] == "已回覆客戶"   # A 的沒被 B 蓋掉
        assert rfi.data["risk"] == "頻寬不足"       # B 的也生效

    async def test_same_field_conflicts(self, client, rfi_form, session):
        """兩人改同一欄成不同值 → 409，並列出雙方的值讓人決定。"""
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        shared_base = await _baseline(session, rfi_id)

        await client.post(
            f"/rfis/{rfi_id}/edit",
            data={**rfi_form(resolution_w="2880", resolution_h="1800"),
                  "baseline": shared_base, "note": "A"},
        )
        resp = await client.post(
            f"/rfis/{rfi_id}/edit",
            data={**rfi_form(resolution_w="3000", resolution_h="2000"),
                  "baseline": shared_base, "note": "B"},
        )
        assert resp.status_code == 409
        assert "編輯衝突" in resp.text
        assert "2880 x 1800" in resp.text   # 對方目前的值
        assert "3000 x 2000" in resp.text   # 自己填的值

    async def test_same_field_same_value_is_not_a_conflict(self, client, rfi_form, session):
        """兩人剛好改成一樣的值，不該擋下來。"""
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        shared_base = await _baseline(session, rfi_id)
        for who in ("A", "B"):
            resp = await client.post(
                f"/rfis/{rfi_id}/edit",
                data={**rfi_form(status="已結案"), "baseline": shared_base, "note": who},
            )
            assert resp.status_code == 303


class TestDelete:
    async def test_admin_can_delete(self, client, rfi_form, session):
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        resp = await client.post(f"/rfis/{rfi_id}/delete")
        assert resp.status_code == 303
        assert (await session.execute(select(Rfi))).scalars().all() == []

    async def test_revisions_cascade(self, client, rfi_form, session):
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        await client.post(f"/rfis/{rfi_id}/delete")
        assert (await session.execute(select(RfiRevision))).scalars().all() == []


class TestPages:
    async def test_all_pages_render(self, client, rfi_form):
        loc = await create_rfi(client, rfi_form())
        for path in ("/rfis", "/dashboard", "/dashboard?group=vendor",
                     loc, f"{loc}/edit", f"{loc}/history", "/import", "/tokens"):
            resp = await client.get(path, headers={"Accept": "text/html"})
            assert resp.status_code == 200, f"{path} → {resp.status_code}"

    async def test_health_and_readiness(self, client):
        assert (await client.get("/healthz")).status_code == 200
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["checks"] == {"database": "ok", "storage": "ok"}
