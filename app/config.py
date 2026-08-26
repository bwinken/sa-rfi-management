"""應用設定 — 從環境變數 / .env 載入。"""

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


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

    # 資料庫：可只給 SQLITE_PATH（檔案路徑），或用 DATABASE_URL 完整覆寫
    SQLITE_PATH: str = os.getenv("SQLITE_PATH", "./sa_rfi.db")
    DATABASE_URL: str = os.getenv("DATABASE_URL") or (
        f"sqlite+aiosqlite:///{os.getenv('SQLITE_PATH', './sa_rfi.db')}"
    )
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "25"))

    # Cookie
    COOKIE_SECURE: bool = _bool("COOKIE_SECURE", "false")

    # 匯出投影片的標題（後面會自動接上週別區間，如 " W29~W35"）
    DECK_TITLE: str = os.getenv("DECK_TITLE", "Customer RFI Collection")

    # 頁尾署名
    DESIGNED_BY: str = os.getenv("DESIGNED_BY", "")
    EXECUTED_BY: str = os.getenv("EXECUTED_BY", "")

    # 日誌（loguru）
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE: str = os.getenv("LOG_FILE", "./logs/sa_rfi.log")

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
    def jwks_url(self) -> str:
        """JWKS 端點；未設定 JWKS_URL 時由 AUTH_CENTER_BASE_URL 推導，空字串表示停用。"""
        if self._JWKS_URL_RAW is None:
            return self.AUTH_CENTER_BASE_URL.rstrip("/") + "/.well-known/jwks.json"
        return self._JWKS_URL_RAW


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
