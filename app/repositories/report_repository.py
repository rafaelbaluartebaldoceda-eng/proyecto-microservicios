from __future__ import annotations

from uuid import UUID

from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.report import (
    ReportEvent,
    ReportEventType,
    ReportFile,
    ReportRequest,
    ReportStatus,
    TaskAttempt,
)


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_request(self, report_request: ReportRequest) -> ReportRequest:
        self.session.add(report_request)
        await self.session.flush()
        return report_request

    async def add_event(
        self,
        report_request_id: UUID,
        event_type: ReportEventType,
        message: str,
        event_metadata: dict | None = None,
    ) -> ReportEvent:
        event = ReportEvent(
            report_request_id=report_request_id,
            event_type=event_type,
            message=message,
            event_metadata=event_metadata or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def add_attempt(self, attempt: TaskAttempt) -> TaskAttempt:
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def create_file(self, report_file: ReportFile) -> ReportFile:
        self.session.add(report_file)
        await self.session.flush()
        return report_file

    async def get_request(self, report_id: UUID) -> ReportRequest | None:
        query = self._detailed_query().where(ReportRequest.id == report_id)
        result = await self.session.execute(query)
        return result.scalars().unique().one_or_none()

    async def count_attempts(self, report_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count(TaskAttempt.id)).where(TaskAttempt.report_request_id == report_id)
        )
        return int(result.scalar_one())

    async def list_requests(
        self,
        *,
        user_id: str | None = None,
        report_type: str | None = None,
        status: ReportStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[ReportRequest]]:
        filters = []
        now = datetime.now(UTC)
        filters.append(ReportRequest.deleted_at.is_(None))
        filters.append(or_(ReportRequest.expires_at.is_(None), ReportRequest.expires_at > now))
        if user_id:
            filters.append(ReportRequest.user_id == user_id)
        if report_type:
            filters.append(ReportRequest.report_type == report_type)
        if status:
            filters.append(ReportRequest.status == status)

        total_stmt = select(func.count(ReportRequest.id)).where(*filters)
        items_stmt = (
            self._detailed_query()
            .where(*filters)
            .order_by(ReportRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        total = int((await self.session.execute(total_stmt)).scalar_one())
        items = (await self.session.execute(items_stmt)).scalars().unique().all()
        return total, list(items)

    async def save(self) -> None:
        await self.session.commit()

    async def refresh(self, instance: ReportRequest) -> None:
        await self.session.refresh(instance)

    def _detailed_query(self) -> Select[tuple[ReportRequest]]:
        return select(ReportRequest).options(
            selectinload(ReportRequest.file),
            selectinload(ReportRequest.events),
            selectinload(ReportRequest.attempts),
        )
