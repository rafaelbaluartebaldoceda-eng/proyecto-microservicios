from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID

from celery import Task

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.report import ReportFile, ReportStatus
from app.services.report_builder import ReportBuilderService
from app.services.report_service import ReportService
from app.services.storage import build_storage_service


logger = logging.getLogger(__name__)
settings = get_settings()
MAX_STORAGE_RETRIES = 3


class TransientStorageError(RuntimeError):
    """Excepcion para disparar autoretry."""


@celery_app.task(
    bind=True,
    autoretry_for=(TransientStorageError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": MAX_STORAGE_RETRIES},
    name="app.tasks.report_tasks.generate_report_task",
)
def generate_report_task(self: Task, report_request_id: str) -> str:
    return asyncio.run(_generate_report(report_request_id=UUID(report_request_id), retry_count=self.request.retries))


async def _generate_report(report_request_id: UUID, retry_count: int) -> str:
    async with AsyncSessionLocal() as session:
        service = ReportService(session)
        report = await service.repository.get_request(report_request_id)
        if not report:
            logger.warning("Report not found", extra={"report_id": str(report_request_id)})
            return str(report_request_id)
        if report.status == ReportStatus.canceled:
            return str(report_request_id)

        if retry_count > 0:
            await service.record_processing_retry(report, f"Reintento numero {retry_count}")

        attempt = await service.record_processing_started(report)
        builder = ReportBuilderService()
        storage = build_storage_service()

        try:
            generated = builder.build_report(
                report_id=report.id,
                report_type=report.report_type,
                report_format=report.file_format,
                filters=report.filters,
            )
            destination_path = str(
                PurePosixPath(str(datetime.now(UTC).year), str(report.id), generated.file_name)
            )
            stored = storage.upload_bytes(
                content=generated.payload,
                destination_path=destination_path,
                content_type=generated.content_type,
            )
        except OSError as exc:
            if retry_count < MAX_STORAGE_RETRIES:
                await service.record_processing_retry_attempt(report, attempt, str(exc))
                raise TransientStorageError(str(exc)) from exc
            await service.record_processing_failure(report, attempt, str(exc))
            raise
        except Exception as exc:
            await service.record_processing_failure(report, attempt, str(exc))
            raise

        await asyncio.sleep(0)
        await service.repository.refresh(report)
        if report.status == ReportStatus.canceled:
            await service.record_processing_canceled(report, attempt)
            return str(report.id)

        report_file = ReportFile(
            report_request_id=report.id,
            file_name=generated.file_name,
            file_type=generated.content_type,
            storage_path=stored.storage_path,
            checksum=generated.checksum,
            size_bytes=stored.size_bytes,
            storage_provider=stored.provider,
        )
        await service.record_processing_success(report, attempt, report_file)
        return str(report.id)
