"""權限邊界與 API Token。

這裡的每一條失敗都代表安全問題，不只是功能壞掉，所以正反兩面都測：
該通過的要通過，該擋的一定要擋。
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app import auth as auth_module
from app.auth import generate_token, hash_token, resolve_api_token
from app.config import settings
from app.models import ApiToken
from tests.conftest import create_rfi


async def _make_token(session, *, owner="test.user", scopes="read",
                      expires_in_days: int | None = 90, revoked=False) -> str:
    raw, prefix, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    session.add(ApiToken(
        name="測試", prefix=prefix, token_hash=token_hash, scopes=scopes, owner=owner,
        expires_at=(now + timedelta(days=expires_in_days)) if expires_in_days else None,
        revoked_at=now if revoked else None,
    ))
    await session.commit()
    return raw


class TestTokenStorage:
    async def test_plaintext_never_stored(self, session):
        raw = await _make_token(session)
        rows = (await session.execute(select(ApiToken))).scalars().all()
        assert all(r.token_hash != raw for r in rows)
        assert rows[0].token_hash == hash_token(raw)

    async def test_prefix_is_recognisable(self, session):
        raw = await _make_token(session)
        assert raw.startswith("sarfi_")
        row = (await session.execute(select(ApiToken))).scalar_one()
        assert raw[len("sarfi_"):].startswith(row.prefix)

    async def test_tokens_are_unique(self):
        assert len({generate_token()[0] for _ in range(200)}) == 200


class TestTokenResolution:
    async def test_valid_token_resolves(self, session):
        raw = await _make_token(session)
        user = await resolve_api_token(session, raw)
        assert user["sub"] == "test.user"
        assert user["scopes"] == ["read"]
        assert user["auth"] == "api_token"

    async def test_unknown_token_rejected(self, session):
        assert await resolve_api_token(session, "sarfi_totallybogus") is None

    async def test_wrong_prefix_rejected(self, session):
        raw = await _make_token(session)
        assert await resolve_api_token(session, raw.replace("sarfi_", "other_")) is None

    async def test_expired_token_rejected(self, session):
        raw = await _make_token(session, expires_in_days=-1)
        assert await resolve_api_token(session, raw) is None

    async def test_revoked_token_rejected(self, session):
        raw = await _make_token(session, revoked=True)
        assert await resolve_api_token(session, raw) is None

    async def test_never_expiring_token_accepted(self, session):
        raw = await _make_token(session, expires_in_days=None)
        assert await resolve_api_token(session, raw) is not None

    async def test_last_used_recorded(self, session):
        raw = await _make_token(session)
        await resolve_api_token(session, raw)
        row = (await session.execute(select(ApiToken))).scalar_one()
        assert row.last_used_at is not None


@pytest.fixture
async def strict_client(monkeypatch):
    """關掉 DEV_AUTH_BYPASS 的 client —— 這才是正式環境的行為。"""
    # settings 是 lru_cache 的單例，直接改屬性即可影響所有讀取它的模組
    monkeypatch.setattr(settings, "DEV_AUTH_BYPASS", False)
    async with auth_app_client() as c:
        yield c


def auth_app_client():
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver",
                             follow_redirects=False)


class TestApiAuth:
    """DEV_AUTH_BYPASS=false 下，API 必須真的擋。"""

    async def test_no_credentials_rejected(self, strict_client):
        resp = await strict_client.get("/api/v1/rfis")
        assert resp.status_code == 401

    async def test_bogus_token_rejected(self, strict_client):
        resp = await strict_client.get(
            "/api/v1/rfis", headers={"Authorization": "Bearer sarfi_nope"})
        assert resp.status_code == 401

    async def test_valid_token_accepted(self, strict_client, session):
        raw = await _make_token(session)
        resp = await strict_client.get(
            "/api/v1/rfis", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200

    async def test_token_identity_reported(self, strict_client, session):
        raw = await _make_token(session, owner="alex.lin")
        resp = await strict_client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {raw}"})
        assert resp.json()["sub"] == "alex.lin"
        assert resp.json()["auth"] == "api_token"

    async def test_api_errors_are_json_not_html(self, strict_client):
        """API 路徑即使瀏覽器來訪也要回 JSON，不能導到 HTML 錯誤頁。"""
        resp = await strict_client.get("/api/v1/rfis", headers={"Accept": "text/html"})
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/json")

    async def test_token_has_no_write_access(self, strict_client, session, rfi_form):
        """即使擁有者本人有 write/admin，token 也只能讀。"""
        raw = await _make_token(session, scopes="read")
        headers = {"Authorization": f"Bearer {raw}", "Accept": "application/json"}
        for path in ("/rfis/new", "/rfis/1/delete", "/import"):
            resp = await strict_client.post(path, headers=headers, data=rfi_form())
            assert resp.status_code == 401, f"{path} 竟然放行了"

    async def test_scope_without_read_rejected(self, strict_client, session):
        raw = await _make_token(session, scopes="write")
        resp = await strict_client.get(
            "/api/v1/rfis", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 403

    async def test_html_pages_redirect_to_login(self, strict_client):
        resp = await strict_client.get("/rfis", headers={"Accept": "text/html"})
        assert resp.status_code == 303
        assert resp.headers["location"].endswith("/login")


class TestScopeEnforcement:
    """DEV_SCOPES 控制的權限分級（模擬 Auth Center 的 level → scopes）。"""

    async def test_read_only_user_cannot_write(self, client, monkeypatch, rfi_form):
        monkeypatch.setattr(settings, "DEV_SCOPES", ["read"])
        assert (await client.get("/rfis", headers={"Accept": "text/html"})).status_code == 200
        for path in ("/rfis/new", "/import"):
            resp = await client.get(path, headers={"Accept": "text/html"})
            assert resp.status_code == 403

    async def test_write_user_cannot_delete(self, client, monkeypatch, rfi_form):
        monkeypatch.setattr(settings, "DEV_SCOPES", ["read", "write"])
        loc = await create_rfi(client, rfi_form())
        rfi_id = int(loc.rsplit("/", 1)[1])
        resp = await client.post(f"/rfis/{rfi_id}/delete", headers={"Accept": "application/json"})
        assert resp.status_code == 403

    async def test_write_hidden_from_nav_for_readers(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DEV_SCOPES", ["read"])
        html = (await client.get("/rfis", headers={"Accept": "text/html"})).text
        assert "新增 RFI" not in html


class TestTokenManagement:
    async def test_create_shows_token_once(self, client):
        resp = await client.post("/tokens", data={"name": "腳本", "expires_days": "90"})
        assert resp.status_code == 200
        assert "sarfi_" in resp.text

    async def test_name_required(self, client):
        resp = await client.post("/tokens", data={"name": "", "expires_days": "90"})
        assert resp.status_code == 400

    async def test_revoke(self, client, session):
        await client.post("/tokens", data={"name": "腳本", "expires_days": "90"})
        row = (await session.execute(select(ApiToken))).scalar_one()
        resp = await client.post(f"/tokens/{row.id}/revoke")
        assert resp.status_code == 303
        await session.refresh(row)
        assert row.revoked_at is not None

    async def test_cannot_revoke_someone_elses(self, client, session):
        await _make_token(session, owner="another.person")
        row = (await session.execute(select(ApiToken))).scalar_one()
        resp = await client.post(f"/tokens/{row.id}/revoke",
                                 headers={"Accept": "application/json"})
        assert resp.status_code == 403


class TestBearerParsing:
    @pytest.mark.parametrize(("header", "expected"), [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Bearer  abc  ", "abc"),
        ("Basic abc", None),
        ("abc", None),
        ("Bearer", None),
        ("Bearer ", None),
        (None, None),
    ])
    def test_parsing(self, header, expected):
        assert auth_module._bearer(header) == expected
