import pytest
import sqlite3
from datetime import datetime, timezone
from uuid import UUID

from app.api.routes import reports as reports_route
from app.core.database import AsyncSessionLocal
from app.core.security import AuthenticatedUser
from app.models.report import ReportStatus
from app.repositories.report_repository import ReportRepository
from app.schemas.report import ReportCreateRequest
from app.services.report_service import ReportService
from app.services.storage import StoredObject
from app.tasks import report_tasks
from app.tasks.report_tasks import TransientStorageError, _generate_report


def build_payload() -> dict:
    return {
        "report_type": "sales_summary",
        "format": "excel",
        "filters": {
            "start_date": "2026-04-01",
            "end_date": "2026-04-10",
            "area": "Finanzas",
            "status": "closed",
            "category": "Q2",
            "requested_user": "ana",
        },
    }


async def create_report_request() -> UUID:
    async with AsyncSessionLocal() as session:
        service = ReportService(session)
        report = await service.create_report(
            ReportCreateRequest.model_validate(build_payload()),
            AuthenticatedUser(user_id="tester-1", email="tester@example.com", roles=["admin"]),
        )
        await service.mark_enqueued(report, str(report.id))
        return report.id

@pytest.mark.asyncio
async def test_report_creation_status_and_download(client, auth_headers):
    payload = build_payload()
    create_response = await client.post("/reports", headers=auth_headers, json=payload)
    assert create_response.status_code == 202
    report_id = create_response.json()["report_id"]

    detail_response = await client.get(f"/reports/{report_id}", headers=auth_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "SUCCESS"

    download_response = await client.get(f"/reports/{report_id}/download", headers=auth_headers)
    assert download_response.status_code == 200
    download_url = download_response.json()["download_url"]

    file_response = await client.get(download_url, headers=auth_headers)
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.asyncio
async def test_list_reports(client, auth_headers):
    response = await client.get("/reports", headers=auth_headers)
    assert response.status_code == 200
    assert "items" in response.json()


@pytest.mark.asyncio
async def test_enqueue_failure_marks_report_as_failed(client, auth_headers, monkeypatch):
    def fail_apply_async(**kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(reports_route.settings, "task_always_eager", False)
    monkeypatch.setattr(reports_route.generate_report_task, "apply_async", fail_apply_async)

    response = await client.post("/reports", headers=auth_headers, json=build_payload())
    assert response.status_code == 503

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        total, items = await repo.list_requests(user_id="tester-1", limit=50)
        assert total >= 1
        failed = [item for item in items if item.status == ReportStatus.failure and item.error_message == "broker down"]
        assert failed


@pytest.mark.asyncio
async def test_retryable_storage_failure_closes_attempt(monkeypatch):
    class FailingStorage:
        def upload_bytes(self, **kwargs):
            raise OSError("temporary storage outage")

        def open_bytes(self, storage_path: str) -> bytes:
            raise NotImplementedError

        def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None:
            return None

    report_id = await create_report_request()

    monkeypatch.setattr(report_tasks, "build_storage_service", lambda: FailingStorage())
    monkeypatch.setattr(report_tasks, "MAX_STORAGE_RETRIES", 3)

    with pytest.raises(TransientStorageError):
        await _generate_report(report_id, retry_count=0)

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(report_id)
        assert report is not None
        assert report.status == ReportStatus.retry
        assert report.attempts[0].status == ReportStatus.retry
        assert report.attempts[0].ended_at is not None


@pytest.mark.asyncio
async def test_final_storage_failure_marks_report_failure(monkeypatch):
    class FailingStorage:
        def upload_bytes(self, **kwargs):
            raise OSError("permanent storage outage")

        def open_bytes(self, storage_path: str) -> bytes:
            raise NotImplementedError

        def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None:
            return None

    report_id = await create_report_request()

    monkeypatch.setattr(report_tasks, "build_storage_service", lambda: FailingStorage())
    monkeypatch.setattr(report_tasks, "MAX_STORAGE_RETRIES", 0)

    with pytest.raises(OSError):
        await _generate_report(report_id, retry_count=0)

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(report_id)
        assert report is not None
        assert report.status == ReportStatus.failure
        assert report.attempts[0].status == ReportStatus.failure
        assert report.attempts[0].ended_at is not None


@pytest.mark.asyncio
async def test_canceled_report_is_not_promoted_to_success(monkeypatch):
    class CancelingStorage:
        def upload_bytes(self, **kwargs):
            connection = sqlite3.connect("./data/test.db")
            try:
                connection.execute(
                    "UPDATE report_requests SET status = ?, completed_at = ? WHERE id = ?",
                    ("canceled", datetime.now(timezone.utc).isoformat(), report_id.hex),
                )
                connection.commit()
            finally:
                connection.close()
            return StoredObject(storage_path="cancel/test.xlsx", size_bytes=4, provider="local")

        def open_bytes(self, storage_path: str) -> bytes:
            raise NotImplementedError

        def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None:
            return None

    report_id = await create_report_request()
    monkeypatch.setattr(report_tasks, "build_storage_service", lambda: CancelingStorage())

    await _generate_report(report_id, retry_count=0)

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(report_id)
        assert report is not None
        assert report.status == ReportStatus.canceled
        assert report.attempts[0].status == ReportStatus.canceled
        assert report.file is None
