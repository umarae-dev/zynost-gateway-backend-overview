import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DeviceToken(Base):
    """Points at the SAME device_tokens table tradeos-backend owns and
    migrates (shared Postgres DB, shared users table - see
    app/core/config.py's docstring on exactly what's shared between the
    two products). Defined here too, in this codebase's own model style,
    so this service can read/write it without ever importing
    tradeos-backend's Python code - a merchant who's also a Zynost trader
    gets pushes from both products on whichever devices they've
    registered, with neither service calling the other over HTTP for it."""
    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    fcm_token: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # "android" | "ios" | "web"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
