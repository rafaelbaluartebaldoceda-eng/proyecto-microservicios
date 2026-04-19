from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings


settings = get_settings()


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str | None = None
    roles: list[str] = []

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


class TokenPayload(BaseModel):
    sub: str
    roles: list[str] = []
    email: str | None = None
    aud: str
    iss: str
    exp: int
    iat: int


REPORT_ROLE_MAP: dict[str, set[str]] = {
    "sales_summary": {"admin", "finance", "sales"},
    "operations_kpis": {"admin", "operations"},
    "audit_log": {"admin", "auditor", "security"},
}


def create_access_token(user: AuthenticatedUser) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "roles": user.roles,
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> AuthenticatedUser:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido o expirado.",
        ) from exc

    data = TokenPayload(**payload)
    return AuthenticatedUser(user_id=data.sub, email=data.email, roles=data.roles)


def ensure_report_permission(user: AuthenticatedUser, report_type: str) -> None:
    if user.is_admin:
        return
    allowed_roles = REPORT_ROLE_MAP.get(report_type, set())
    if allowed_roles.intersection(user.roles):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="No tienes permisos para generar este reporte.",
    )

