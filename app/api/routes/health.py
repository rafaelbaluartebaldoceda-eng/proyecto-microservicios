from __future__ import annotations

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings
from app.schemas.report import HealthComponent, HealthResponse


router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
async def healthcheck(session: DbSession) -> HealthResponse:
    components: list[HealthComponent] = []
    db_ok = False
    redis_ok = False

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
        components.append(HealthComponent(name="database", ok=True, detail="Conexion operativa."))
    except Exception as exc:
        components.append(HealthComponent(name="database", ok=False, detail=str(exc)))

    redis = Redis.from_url(settings.redis_url)
    try:
        redis_ok = bool(await redis.ping())
        components.append(HealthComponent(name="redis", ok=redis_ok, detail="Broker disponible."))
    except Exception as exc:
        components.append(HealthComponent(name="redis", ok=False, detail=str(exc)))
    finally:
        await redis.aclose()

    overall = "ok" if db_ok and redis_ok else "degraded"
    return HealthResponse(status=overall, components=components)
