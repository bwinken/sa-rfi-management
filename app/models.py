"""資料模型：RFI 案件、修改紀錄、附件。

RFI 的所有欄位以 JSON 形式存放在 Rfi.data，方便整份快照寫入修改紀錄
（RfiRevision）與彈性擴充欄位；列表常用的欄位另外萃取成資料表欄位以利查詢。
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Rfi(Base):
    """一筆客戶 RFI 案件（目前最新版本）。"""

    __tablename__ = "rfis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 人為可讀的案件編號，建立時產生後不再變動（如 R26W29-01）
    rfi_no: Mapped[str] = mapped_column(String(32), default="", index=True)

    # 從 data 萃取出的便利欄位，供列表、篩選與統計使用
    rfi_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    week: Mapped[str] = mapped_column(String(8), default="", index=True)
    client: Mapped[str] = mapped_column(String(64), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="", index=True)
    product: Mapped[str] = mapped_column(String(32), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="", index=True)
    ic: Mapped[str] = mapped_column(String(64), default="", index=True)

    data: Mapped[dict] = mapped_column(JSON, default=dict)

    created_by: Mapped[str] = mapped_column(String(128), default="")
    updated_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    version: Mapped[int] = mapped_column(Integer, default=1)

    revisions: Mapped[list["RfiRevision"]] = relationship(
        back_populates="rfi",
        cascade="all, delete-orphan",
        order_by="RfiRevision.id.desc()",  # 依時間倒序，形成完整時間綫
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="rfi",
        cascade="all, delete-orphan",
        order_by="Attachment.id.desc()",
    )


class RfiRevision(Base):
    """RFI 的歷史快照 — 每次新增／編輯／附件異動都寫入一筆。"""

    __tablename__ = "rfi_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rfi_id: Mapped[int] = mapped_column(
        ForeignKey("rfis.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    # 相對於前一版本變動的欄位清單（list[dict]）
    changes: Mapped[list] = mapped_column(JSON, default=list)
    # create / update / attachment / import
    action: Mapped[str] = mapped_column(String(16), default="update")
    note: Mapped[str] = mapped_column(Text, default="")
    edited_by: Mapped[str] = mapped_column(String(128), default="")
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    rfi: Mapped["Rfi"] = relationship(back_populates="revisions")


class Attachment(Base):
    """RFI 附件（客戶 spec、來信截圖、報價單等）。"""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rfi_id: Mapped[int] = mapped_column(
        ForeignKey("rfis.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))         # 顯示用原始檔名
    stored_name: Mapped[str] = mapped_column(String(512))      # 相對 UPLOAD_DIR 的路徑
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(128), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    rfi: Mapped["Rfi"] = relationship(back_populates="attachments")


class ApiToken(Base):
    """個人 API Token — 供程式化讀取使用。

    Auth Center 的 JWT 只有 12 小時且沒有 refresh，不適合放在腳本或
    MCP 設定檔裡。這裡讓使用者自行簽發長期有效、可隨時撤銷的唯讀 token：
    只在建立當下顯示一次，資料庫只存 SHA-256 雜湊。
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 使用者自訂的用途說明，例如「週報自動化腳本」
    name: Mapped[str] = mapped_column(String(128), default="")
    # 顯示用的前綴（token 的前幾碼），讓使用者在列表中認得出是哪一支
    prefix: Mapped[str] = mapped_column(String(16), default="", index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 目前一律為 "read"；寫入操作不開放給 token
    scopes: Mapped[str] = mapped_column(String(64), default="read")

    owner: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def scope_list(self) -> list[str]:
        return [s for s in self.scopes.split(",") if s]
