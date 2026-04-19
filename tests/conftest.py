import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test.db"
os.environ["SYNC_DATABASE_URL"] = "sqlite:///./data/test.db"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["TASK_ALWAYS_EAGER"] = "true"
os.environ["LOCAL_STORAGE_PATH"] = "./storage/test"

from app.core.database import init_db
from app.core.security import AuthenticatedUser, create_access_token
from app.main import app


@pytest_asyncio.fixture(scope="session", autouse=True)
async def initialize_db() -> None:
    await init_db()


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    token = create_access_token(
        AuthenticatedUser(
            user_id="tester-1",
            email="tester@example.com",
            roles=["admin", "finance", "operations", "auditor"],
        )
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as async_client:
        yield async_client
