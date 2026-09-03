"""應用設定 — 從環境變數 / .env 載入。"""

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _normalize_db_url(url: str) -> str:
    """把同步版的連線字串換成 async driver。

    大家手上的 DATABASE_URL 通常是 `postgresql://...`（給 psycopg 用的），
    但本 App 全程 async，需要 asyncpg。與其要求每個人記得改，不如在這裡補上。
    """
    prefixes = {
        "postgresql://": "postgresql+asyncpg://",
        "postgres://": "postgresql+asyncpg://",
        "sqlite://": "sqlite+aiosqlite://",
    }
    for old, new in prefixes.items():
        if url.startswith(old):
            return new + url[len(old):]
    return url


class Settings:
    # 本 App 對外的 Base URL（OAuth callback、絕對連結皆以此為基礎）
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8003").rstrip("/")

    # 反向代理子路徑前綴（如部署在 http://host/sa-rfi-management/ → "/sa-rfi-management"）。
    # 未設定時自動由 APP_BASE_URL 的路徑推導；根目錄部署則為 ""。
    ROOT_PATH: str = (
        os.getenv("ROOT_PATH", urlparse(os.getenv("APP_BASE_URL", "")).path)
    ).rstrip("/")

    # Auth Center
    AUTH_CENTER_BASE_URL: str = os.getenv("AUTH_CENTER_BASE_URL", "http://localhost:8000")
    APP_ID: str = os.getenv("APP_ID", "sa_rfi_management")
    CLIENT_SECRET: str = os.getenv("CLIENT_SECRET", "sa_rfi_secret_change_me")
    # 未指定時，自動以 APP_BASE_URL 組出 callback
    REDIRECT_URI: str = os.getenv("REDIRECT_URI") or (
        os.getenv("APP_BASE_URL", "http://localhost:8003").rstrip("/") + "/auth/callback"
    )
    PUBLIC_KEY_PATH: str = os.getenv("PUBLIC_KEY_PATH", "./keys/public.pem")
    ALGORITHM: str = "RS256"
    # Auth Center 的 JWKS 端點（OIDC 標準）；用來自動取得驗章公鑰，免去
    # 手動維護 public.pem。未設定時自動由 AUTH_CENTER_BASE_URL 推導；
    # 設為空字串可停用 JWKS、只用本地 PEM。
    _JWKS_URL_RAW = os.getenv("JWKS_URL")

    # ── 狀態存放位置 ─────────────────────────────────────────
    # 這個 App 的容器本身是 stateless 的：所有會被寫入、且重啟後必須還在的
    # 東西（SQLite 檔、附件）都放在 DATA_DIR 底下，部署時把它掛成 volume。
    # 個別路徑仍可用 SQLITE_PATH / UPLOAD_DIR 覆寫（相容既有的本機設定）。
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "."))
    SQLITE_PATH: str = os.getenv("SQLITE_PATH") or str(DATA_DIR / "sa_rfi.db")
    DATABASE_URL: str = _normalize_db_url(
        os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{SQLITE_PATH}"
    )
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR") or (DATA_DIR / "uploads"))
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "25"))

    # 靜態資源版本（快取破壞用）。多副本部署時請設成 image tag / commit sha，
    # 讓每個副本回傳同一個值；未設定則以啟動時間為準。
    ASSET_VERSION: str = os.getenv("ASSET_VERSION", "")

    # Cookie
    COOKIE_SECURE: bool = _bool("COOKIE_SECURE", "false")

    # 匯出投影片的標題（後面會自動接上週別區間，如 " W29~W35"）
    DECK_TITLE: str = os.getenv("DECK_TITLE", "Customer RFI Collection")

    # 頁尾署名
    DESIGNED_BY: str = os.getenv("DESIGNED_BY", "")
    EXECUTED_BY: str = os.getenv("EXECUTED_BY", "")

    # 日誌（loguru）—— 預設只輸出到 stderr，由容器平台收走（12-factor）。
    # 只有在傳統主機部署、希望自己留輪替檔時才設定 LOG_FILE。
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE: str = os.getenv("LOG_FILE", "")

    # 開發模式
    DEV_AUTH_BYPASS: bool = _bool("DEV_AUTH_BYPASS", "false")
    DEV_USER: str = os.getenv("DEV_USER", "dev.user")
    DEV_SCOPES: list[str] = [
        s.strip() for s in os.getenv("DEV_SCOPES", "read,write,admin").split(",") if s.strip()
    ]

    @property
    def login_url(self) -> str:
        return (
            f"{self.AUTH_CENTER_BASE_URL}/auth/login"
            f"?app_id={self.APP_ID}&redirect_uri={self.REDIRECT_URI}"
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def state_paths(self) -> list[Path]:
        """啟動時必須存在且可寫的目錄（SQLite 檔所在目錄 + 附件目錄）。"""
        paths = [self.UPLOAD_DIR]
        if self.is_sqlite:
            paths.append(Path(self.SQLITE_PATH).parent)
        return paths

    @property
    def jwks_url(self) -> str:
        """JWKS 端點；未設定 JWKS_URL 時由 AUTH_CENTER_BASE_URL 推導，空字串表示停用。"""
        if self._JWKS_URL_RAW is None:
            return self.AUTH_CENTER_BASE_URL.rstrip("/") + "/.well-known/jwks.json"
        return self._JWKS_URL_RAW


def prepare_storage(s: "Settings") -> None:
    """建立並驗證狀態目錄，不可寫就直接讓程式啟動失敗。

    容器化部署最常見的錯就是 volume 沒掛上、或掛上了但 UID 對不上：
    這種情況下 App 會「看起來正常啟動」，直到有人上傳附件才炸。
    寧可在啟動時就失敗，讓 orchestrator 直接把它標成不健康。
    """
    for path in s.state_paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"無法建立狀態目錄 {path}：{e}。"
                f"請確認 DATA_DIR（目前為 {s.DATA_DIR}）已掛載且容器使用者有權限。"
            ) from e
        probe = path / ".write-probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            raise RuntimeError(
                f"狀態目錄 {path} 不可寫：{e}。"
                f"容器以 UID {os.getuid()} 執行，請把 volume 的擁有者改成這個 UID"
                f"（或在 Kubernetes 設定 securityContext.fsGroup）。"
            ) from e


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
