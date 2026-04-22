import sqlite3
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.api.routes import reports as reports_route
from app.core.database import AsyncSessionLocal
from app.core.security import AuthenticatedUser, create_access_token
from fastapi import HTTPException
from app.models.report import ReportEventType, ReportStatus
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
    assert file_response.headers["x-report-id"] == report_id
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

        def exists(self, storage_path: str) -> bool:
            return False

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

        def exists(self, storage_path: str) -> bool:
            return False

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

        def exists(self, storage_path: str) -> bool:
            return True

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


@pytest.mark.asyncio
async def test_failed_report_cannot_be_canceled():
    report_id = await create_report_request()

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(report_id)
        assert report is not None
        report.status = ReportStatus.failure
        report.completed_at = datetime.now(timezone.utc)
        await repo.save()

    async with AsyncSessionLocal() as session:
        service = ReportService(session)
        with pytest.raises(HTTPException) as exc_info:
            await service.cancel_report(
                report_id,
                AuthenticatedUser(user_id="tester-1", email="tester@example.com", roles=["admin"]),
            )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_download_missing_file_returns_404_without_completed_event(client, auth_headers):
    create_response = await client.post("/reports", headers=auth_headers, json=build_payload())
    report_id = create_response.json()["report_id"]

    token_response = await client.get(f"/reports/{report_id}/download", headers=auth_headers)
    download_url = token_response.json()["download_url"]

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(UUID(report_id))
        assert report is not None
        storage_path = reports_route.settings.local_storage_path / report.file.storage_path
        storage_path.unlink()

    file_response = await client.get(download_url, headers=auth_headers)
    assert file_response.status_code == 404

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(UUID(report_id))
        assert report is not None
        event_types = [event.event_type for event in report.events]
        assert ReportEventType.download_requested in event_types
        assert ReportEventType.download_completed not in event_types


@pytest.mark.asyncio
async def test_expired_report_is_gone_and_hidden_from_list(client, auth_headers):
    create_response = await client.post("/reports", headers=auth_headers, json=build_payload())
    report_id = create_response.json()["report_id"]

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(UUID(report_id))
        assert report is not None
        report.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await repo.save()

    list_response = await client.get("/reports", headers=auth_headers)
    assert list_response.status_code == 200
    listed_ids = {item["id"] for item in list_response.json()["items"]}
    assert report_id not in listed_ids

    detail_response = await client.get(f"/reports/{report_id}", headers=auth_headers)
    assert detail_response.status_code == 410


@pytest.mark.asyncio
async def test_expired_report_returns_403_for_user_without_visibility(client, auth_headers):
    create_response = await client.post("/reports", headers=auth_headers, json=build_payload())
    report_id = create_response.json()["report_id"]

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(UUID(report_id))
        assert report is not None
        report.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await repo.save()

    outsider_headers = {
        "Authorization": "Bearer "
        + create_access_token(
            AuthenticatedUser(user_id="outsider-1", email="outsider@example.com", roles=["finance"])
        )
    }
    response = await client.get(f"/reports/{report_id}", headers=outsider_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_s3_redirect_does_not_register_download_completed(client, auth_headers, monkeypatch):
    class RedirectingStorage:
        def upload_bytes(self, **kwargs):
            raise NotImplementedError

        def open_bytes(self, storage_path: str) -> bytes:
            raise NotImplementedError

        def exists(self, storage_path: str) -> bool:
            return True

        def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None:
            return "https://storage.example.com/reports/file.xlsx?signature=abc"

    create_response = await client.post("/reports", headers=auth_headers, json=build_payload())
    report_id = create_response.json()["report_id"]

    token_response = await client.get(f"/reports/{report_id}/download", headers=auth_headers)
    download_url = token_response.json()["download_url"]

    monkeypatch.setattr(reports_route, "build_storage_service", lambda: RedirectingStorage())

    file_response = await client.get(download_url, headers=auth_headers, follow_redirects=False)
    assert file_response.status_code == 307
    assert file_response.headers["location"].startswith("https://storage.example.com/")

    async with AsyncSessionLocal() as session:
        repo = ReportRepository(session)
        report = await repo.get_request(UUID(report_id))
        assert report is not None
        event_types = [event.event_type for event in report.events]
        assert event_types.count(ReportEventType.download_requested) == 1
        assert ReportEventType.download_completed not in event_types
