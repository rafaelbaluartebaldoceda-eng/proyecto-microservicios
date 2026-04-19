from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.report import ReportEventType, ReportFormat, ReportStatus, ReportType


class ReportFilters(BaseModel):
    start_date: date
    end_date: date
    area: str | None = Field(default=None, max_length=80)
    status: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=80)
    requested_user: str | None = Field(default=None, max_length=80)

    @field_validator("area", "status", "category", "requested_user")
    @classmethod
    def sanitize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        sanitized = value.strip()
        if not sanitized:
            return None
        return sanitized

    @model_validator(mode="after")
    def validate_dates(self) -> "ReportFilters":
        if self.end_date < self.start_date:
            raise ValueError("end_date no puede ser menor que start_date")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("El rango de fechas no puede superar 366 dias")
        return self


class ReportCreateRequest(BaseModel):
    report_type: ReportType
    format: ReportFormat
    filters: ReportFilters


class ReportFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_type: str
    storage_path: str
    checksum: str
    size_bytes: int
    storage_provider: str
    created_at: datetime


class ReportEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: ReportEventType
    message: str
    event_metadata: dict[str, Any]
    created_at: datetime


class TaskAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    attempt_no: int
    status: ReportStatus
    started_at: datetime | None
    ended_at: datetime | None
    error_message: str | None
    created_at: datetime


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: str
    report_type: ReportType
    file_format: ReportFormat
    status: ReportStatus
    filters: dict[str, Any]
    correlation_id: str
    celery_task_id: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime | None
    file: ReportFileRead | None = None
    events: list[ReportEventRead] = []
    attempts: list[TaskAttemptRead] = []


class ReportCreatedResponse(BaseModel):
    report_id: UUID
    status: ReportStatus
    message: str


class ReportListResponse(BaseModel):
    total: int
    items: list[ReportRead]


class DownloadLinkResponse(BaseModel):
    report_id: UUID
    download_url: str
    expires_at: datetime


class HealthComponent(BaseModel):
    name: str
    ok: bool
    detail: str


class HealthResponse(BaseModel):
    status: str
    components: list[HealthComponent]
