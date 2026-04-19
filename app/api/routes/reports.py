from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse

from app.api.deps import DbSession, get_current_user
from app.core.config import get_settings
from app.core.security import AuthenticatedUser, ensure_report_permission
from app.models.report import ReportStatus
from app.schemas.report import (
    DownloadLinkResponse,
    ReportCreateRequest,
    ReportCreatedResponse,
    ReportListResponse,
    ReportRead,
)
from app.services.report_service import ReportService
from app.services.storage import LocalStorageService, build_storage_service
from app.tasks.report_tasks import _generate_report, generate_report_task


router = APIRouter(prefix="/reports", tags=["reports"])
settings = get_settings()


@router.post("", response_model=ReportCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_report(
    payload: ReportCreateRequest,
    session: DbSession,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ReportCreatedResponse:
    ensure_report_permission(user, payload.report_type.value)
    service = ReportService(session)
    report = await service.create_report(payload, user)
    if settings.task_always_eager:
        task_id = str(report.id)
        await service.mark_enqueued(report, task_id)
        await _generate_report(report.id, retry_count=0)
    else:
        task = generate_report_task.apply_async(kwargs={"report_request_id": str(report.id)}, task_id=str(report.id))
        await service.mark_enqueued(report, task.id)
    return ReportCreatedResponse(
        report_id=report.id,
        status=report.status,
        message="Solicitud aceptada y enviada a procesamiento asíncrono.",
    )


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: UUID,
    session: DbSession,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ReportRead:
    service = ReportService(session)
    report = await service.get_report(report_id, user)
    return ReportRead.model_validate(report)


@router.get("", response_model=ReportListResponse)
async def list_reports(
    session: DbSession,
    user: AuthenticatedUser = Depends(get_current_user),
    report_type: str | None = Query(default=None),
    status_filter: ReportStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReportListResponse:
    service = ReportService(session)
    total, items = await service.list_reports(
        user=user,
        report_type=report_type,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return ReportListResponse(total=total, items=[ReportRead.model_validate(item) for item in items])


@router.delete("/{report_id}", response_model=ReportRead)
async def cancel_report(
    report_id: UUID,
    session: DbSession,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ReportRead:
    service = ReportService(session)
    report = await service.cancel_report(report_id, user)
    return ReportRead.model_validate(report)


@router.get("/{report_id}/download", response_model=DownloadLinkResponse | None)
async def download_report(
    report_id: UUID,
    request: Request,
    response: Response,
    session: DbSession,
    user: AuthenticatedUser = Depends(get_current_user),
    token: str | None = None,
) -> DownloadLinkResponse | FileResponse | RedirectResponse:
    service = ReportService(session)
    report = await service.get_report(report_id, user)

    if token is None:
        signed_token, expires_at = await service.generate_download_link(report)
        await service.register_download_event(report.id, user.user_id, phase="requested")
        download_url = str(request.url.include_query_params(token=signed_token))
        return DownloadLinkResponse(report_id=report.id, download_url=download_url, expires_at=expires_at)

    service.validate_download_token(token, report)
    storage = build_storage_service()
    presigned_url = storage.create_presigned_download_url(report.file.storage_path, expires_in=300) if report.file else None
    await service.register_download_event(report.id, user.user_id, phase="completed")
    if presigned_url:
        return RedirectResponse(presigned_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    if not isinstance(storage, LocalStorageService):
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    local_path = storage.base_path / report.file.storage_path
    response.headers["X-Report-ID"] = str(report.id)
    return FileResponse(
        path=local_path,
        media_type=report.file.file_type,
        filename=report.file.file_name,
    )
