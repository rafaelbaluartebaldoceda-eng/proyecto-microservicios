import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base


JsonType = JSON().with_variant(JSONB, "postgresql")


class ReportType(str, enum.Enum):
    sales_summary = "sales_summary"
    operations_kpis = "operations_kpis"
    audit_log = "audit_log"


class ReportFormat(str, enum.Enum):
    excel = "excel"
    pdf = "pdf"


class ReportStatus(str, enum.Enum):
    pending = "PENDING"
    started = "STARTED"
    success = "SUCCESS"
    failure = "FAILURE"
    retry = "RETRY"
    canceled = "CANCELED"


class ReportEventType(str, enum.Enum):
    requested = "REQUESTED"
    enqueued = "ENQUEUED"
    started = "STARTED"
    retried = "RETRIED"
    succeeded = "SUCCEEDED"
    failed = "FAILED"
    canceled = "CANCELED"
    download_requested = "DOWNLOAD_REQUESTED"
    download_completed = "DOWNLOAD_COMPLETED"


class ReportRequest(Base):
    __tablename__ = "report_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    report_type: Mapped[ReportType] = mapped_column(Enum(ReportType), index=True)
    file_format: Mapped[ReportFormat] = mapped_column(Enum(ReportFormat))
    status: Mapped[ReportStatus] = mapped_column(Enum(ReportStatus), default=ReportStatus.pending, index=True)
    filters: Mapped[dict] = mapped_column(JsonType)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    file: Mapped["ReportFile | None"] = relationship(back_populates="report_request", uselist=False, cascade="all, delete-orphan")
    events: Mapped[list["ReportEvent"]] = relationship(back_populates="report_request", cascade="all, delete-orphan")
    attempts: Mapped[list["TaskAttempt"]] = relationship(back_populates="report_request", cascade="all, delete-orphan")


class ReportFile(Base):
    __tablename__ = "report_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("report_requests.id", ondelete="CASCADE"), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(32))
    storage_path: Mapped[str] = mapped_column(String(512))
    checksum: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_provider: Mapped[str] = mapped_column(String(32), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report_request: Mapped[ReportRequest] = relationship(back_populates="file")


class ReportEvent(Base):
    __tablename__ = "report_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("report_requests.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[ReportEventType] = mapped_column(Enum(ReportEventType), index=True)
    message: Mapped[str] = mapped_column(String(500))
    event_metadata: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report_request: Mapped[ReportRequest] = relationship(back_populates="events")


class TaskAttempt(Base):
    __tablename__ = "task_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("report_requests.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[ReportStatus] = mapped_column(Enum(ReportStatus))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report_request: Mapped[ReportRequest] = relationship(back_populates="attempts")


Index("ix_report_requests_user_status", ReportRequest.user_id, ReportRequest.status)
