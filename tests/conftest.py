"""測試共用設定。

重點：`app.config` 與 `app.database` 在 import 當下就會讀環境變數並建立
engine，所以環境變數必須在**任何 app 模組被 import 之前**設定好。
conftest 在收集測試前就會被執行，因此這裡是唯一正確的位置。
"""

import os
import tempfile
from pathlib import Path

# ── 必須在 import app 之前 ────────────────────────────────────
_TMP = Path(tempfile.mkdtemp(prefix="sa-rfi-tests-"))
os.environ.update(
    DATA_DIR=str(_TMP),
    DATABASE_URL="",                 # 留空 → 用 DATA_DIR 底下的 SQLite
    LOG_FILE="",                     # 測試不寫日誌檔
    LOG_LEVEL="WARNING",
    DEV_AUTH_BYPASS="true",          # 預設以「已登入且有全部權限」執行
    DEV_USER="test.user",
    DEV_SCOPES="read,write,admin",
    APP_BASE_URL="http://testserver",
    ASSET_VERSION="test",
)
# 允許用 SA_RFI_TEST_DATABASE_URL 指向 PostgreSQL，讓 CI 兩種資料庫都跑
if os.environ.get("SA_RFI_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["SA_RFI_TEST_DATABASE_URL"]

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ApiToken, Attachment, Rfi, RfiRevision  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _tmpdir_note():
    """讓測試失敗時看得到暫存目錄位置。"""
    yield
    # 不刪除：CI 上留著也無妨，本機除錯時用得到


@pytest.fixture(autouse=True)
async def clean_db():
    """每個測試前清空資料表，測試後釋放連線池。

    釋放連線池是 PostgreSQL 必要的：asyncpg 的連線綁在建立它的 event loop
    上，而 pytest-asyncio 每個測試用新的 loop，沿用池中的舊連線會噴
    "attached to a different loop"。SQLite（aiosqlite 走執行緒）沒這問題，
    但一併處理比較單純。
    """
    await init_db()
    async with SessionLocal() as session:
        # 依外鍵順序刪除
        for model in (Attachment, RfiRevision, Rfi, ApiToken):
            await session.execute(delete(model))
        await session.commit()
    yield
    await engine.dispose()


@pytest.fixture
async def client():
    """跑完整 lifespan 的 async client（與正式啟動路徑一致）。"""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=False
        ) as c:
            yield c


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
def rfi_form():
    """一份完整、合法的 RFI 表單資料。測試可覆寫個別欄位。"""
    def _make(**overrides):
        data = {
            "rfi_date": "2026-08-24",
            "client": "HP",
            "vendor": "BOE",
            "product": "OLED NB",
            "code": "Pro-14",
            "ic": "NT3670B",
            "size": "14.0",
            "resolution_w": "2560",
            "resolution_h": "1600",
            "refresh_rate": "60",
            "mux": "None",
            "panel_type": "COF，柔",
            "special": "HDR400",
            "status": "評估中",
            "owner": "Alex Lin",
            "contact": "Jenny Wu",
            "notes": "色域規格確認中。",
            "risk": "無",
        }
        data.update(overrides)
        return data
    return _make


async def create_rfi(client: httpx.AsyncClient, form: dict, note: str = "建立") -> str:
    """建立一筆 RFI，回傳它的 URL 路徑（/rfis/{id}）。"""
    resp = await client.post("/rfis/new", data={**form, "note": note})
    assert resp.status_code == 303, resp.text[:400]
    return resp.headers["location"]


def settings_for_tests():
    return settings


def engine_for_tests():
    return engine
