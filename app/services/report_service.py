from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from celery.exceptions import CeleryError
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_request_id
from app.core.security import AuthenticatedUser
from app.models.report import (
    ReportEventType,
    ReportFile,
    ReportFormat,
    ReportRequest,
    ReportStatus,
    TaskAttempt,
)
from app.repositories.report_repository import ReportRepository
from app.schemas.report import ReportCreateRequest


logger = logging.getLogger(__name__)
settings = get_settings()


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ReportRepository(session)

    async def create_report(self, payload: ReportCreateRequest, user: AuthenticatedUser) -> ReportRequest:
        report = ReportRequest(
            user_id=user.user_id,
            report_type=payload.report_type,
            file_format=payload.format,
            status=ReportStatus.pending,
            filters=payload.filters.model_dump(mode="json"),
            correlation_id=get_request_id(),
            expires_at=datetime.now(UTC) + timedelta(days=settings.report_retention_days),
        )
        await self.repository.create_request(report)
        await self.repository.add_event(
            report.id,
            ReportEventType.requested,
            "Solicitud registrada y validada.",
            {"user_id": user.user_id},
        )
        await self.repository.save()
        await self.repository.refresh(report)
        return report

    async def mark_enqueued(self, report: ReportRequest, task_id: str) -> ReportRequest:
        report.celery_task_id = task_id
        await self.repository.add_event(
            report.id,
            ReportEventType.enqueued,
            "Solicitud enviada a la cola de procesamiento.",
            {"task_id": task_id},
        )
        await self.repository.save()
        await self.repository.refresh(report)
        return report

    async def get_report(self, report_id: UUID, user: AuthenticatedUser) -> ReportRequest:
        report = await self.repository.get_request(report_id)
        if not report:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reporte no encontrado.")
        self._ensure_report_visibility(report, user)
        return report

    async def list_reports(
        self,
        *,
        user: AuthenticatedUser,
        report_type: str | None,
        status_filter: ReportStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[ReportRequest]]:
        total, items = await self.repository.list_requests(
            user_id=None if user.is_admin else user.user_id,
            report_type=report_type,
            status=status_filter,
            limit=min(limit, settings.max_page_size),
            offset=offset,
        )
        return total, items

    async def cancel_report(self, report_id: UUID, user: AuthenticatedUser) -> ReportRequest:
        report = await self.get_report(report_id, user)
        if report.status == ReportStatus.success:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se puede cancelar un reporte finalizado.",
            )
        if report.status == ReportStatus.canceled:
            return report

        report.status = ReportStatus.canceled
        report.completed_at = datetime.now(UTC)
        if report.celery_task_id:
            try:
                from app.core.celery_app import celery_app

                celery_app.control.revoke(report.celery_task_id, terminate=False)
            except CeleryError:
                logger.warning("No se pudo revocar la tarea celery", extra={"report_id": str(report.id)})
        await self.repository.add_event(
            report.id,
            ReportEventType.canceled,
            "Solicitud cancelada por el usuario.",
            {"user_id": user.user_id},
        )
        await self.repository.save()
        await self.repository.refresh(report)
        return report

    async def generate_download_link(self, report: ReportRequest) -> tuple[str, datetime]:
        if report.status != ReportStatus.success or not report.file:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El archivo aun no esta disponible para descarga.",
            )
        expiration = datetime.now(UTC) + timedelta(seconds=settings.download_token_ttl_seconds)
        token = jwt.encode(
            {
                "sub": str(report.id),
                "storage_path": report.file.storage_path,
                "exp": expiration,
            },
            settings.download_token_secret,
            algorithm=settings.jwt_algorithm,
        )
        return token, expiration

    def validate_download_token(self, token: str, report: ReportRequest) -> None:
        try:
            payload = jwt.decode(
                token,
                settings.download_token_secret,
                algorithms=[settings.jwt_algorithm],
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de descarga invalido o expirado.",
            ) from exc
        if payload.get("sub") != str(report.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token no corresponde al reporte.")

    async def register_download_event(self, report_id: UUID, user_id: str, phase: str) -> None:
        event_type = (
            ReportEventType.download_requested if phase == "requested" else ReportEventType.download_completed
        )
        message = "Descarga solicitada." if phase == "requested" else "Descarga completada."
        await self.repository.add_event(report_id, event_type, message, {"user_id": user_id})
        await self.repository.save()

    async def record_processing_started(self, report: ReportRequest) -> TaskAttempt:
        attempt_number = await self.repository.count_attempts(report.id) + 1
        report.status = ReportStatus.started
        report.started_at = datetime.now(UTC)
        report.error_message = None
        attempt = TaskAttempt(
            report_request_id=report.id,
            attempt_no=attempt_number,
            status=ReportStatus.started,
            started_at=datetime.now(UTC),
        )
        await self.repository.add_attempt(attempt)
        await self.repository.add_event(
            report.id,
            ReportEventType.started,
            "Worker inicio la generacion del reporte.",
            {"attempt": attempt_number},
        )
        await self.repository.save()
        return attempt

    async def record_processing_retry(self, report: ReportRequest, error_message: str) -> None:
        report.status = ReportStatus.retry
        report.error_message = error_message
        await self.repository.add_event(
            report.id,
            ReportEventType.retried,
            "Se programo un reintento automatico.",
            {"reason": error_message},
        )
        await self.repository.save()

    async def record_processing_success(
        self,
        report: ReportRequest,
        attempt: TaskAttempt,
        generated_file: ReportFile,
    ) -> ReportRequest:
        report.status = ReportStatus.success
        report.completed_at = datetime.now(UTC)
        report.error_message = None
        attempt.status = ReportStatus.success
        attempt.ended_at = datetime.now(UTC)
        await self.repository.create_file(generated_file)
        await self.repository.add_event(
            report.id,
            ReportEventType.succeeded,
            "Reporte generado correctamente.",
            {
                "file_name": generated_file.file_name,
                "size_bytes": generated_file.size_bytes,
            },
        )
        await self.repository.save()
        await self.repository.refresh(report)
        return report

    async def record_processing_failure(self, report: ReportRequest, attempt: TaskAttempt, error_message: str) -> None:
        report.status = ReportStatus.failure
        report.error_message = error_message
        report.completed_at = datetime.now(UTC)
        attempt.status = ReportStatus.failure
        attempt.ended_at = datetime.now(UTC)
        attempt.error_message = error_message
        await self.repository.add_event(
            report.id,
            ReportEventType.failed,
            "La generacion del reporte fallo.",
            {"error": error_message},
        )
        await self.repository.save()
        logger.exception("Report generation failed", extra={"report_id": str(report.id)})

    def _ensure_report_visibility(self, report: ReportRequest, user: AuthenticatedUser) -> None:
        if user.is_admin or report.user_id == user.user_id:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes acceder a este reporte.")
