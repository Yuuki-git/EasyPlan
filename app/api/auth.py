import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.thread import AgentThread
from app.models.user import User


JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_TTL_MINUTES = 60
PASSWORD_HASH_ITERATIONS = 210_000


class DuplicateEmailError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


@dataclass
class AuthUser:
    id: UUID
    email: str
    password_hash: str
    display_name: str | None = None
    status: str = "active"


class DatabaseUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        display_name: str | None = None,
    ) -> AuthUser:
        user = User(
            id=uuid4(),
            email=normalize_email(email),
            password_hash=password_hash,
            display_name=display_name,
            status="active",
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateEmailError(user.email) from exc
        await self.session.refresh(user)
        return _auth_user_from_model(user)

    async def get_by_email(self, email: str) -> AuthUser | None:
        result = await self.session.execute(select(User).where(User.email == normalize_email(email)))
        user = result.scalar_one_or_none()
        return _auth_user_from_model(user) if user is not None else None

    async def get_by_id(self, user_id: UUID) -> AuthUser | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        return _auth_user_from_model(user) if user is not None else None


class AuthService:
    def __init__(
        self,
        *,
        jwt_secret: str,
        access_token_ttl: timedelta = timedelta(minutes=DEFAULT_ACCESS_TOKEN_TTL_MINUTES),
    ) -> None:
        self.jwt_secret = jwt_secret
        self.access_token_ttl = access_token_ttl

    async def register_user(
        self,
        repository: DatabaseUserRepository,
        email: str,
        password: str,
        display_name: str | None = None,
    ) -> AuthUser:
        normalized_email = normalize_email(email)
        if await repository.get_by_email(normalized_email) is not None:
            raise DuplicateEmailError(normalized_email)
        return await repository.create_user(
            email=normalized_email,
            password_hash=hash_password(password),
            display_name=display_name,
        )

    async def authenticate_user(
        self,
        repository: DatabaseUserRepository,
        email: str,
        password: str,
    ) -> AuthUser:
        user = await repository.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")
        return user

    def issue_access_token(self, user: AuthUser) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "iat": int(now.timestamp()),
            "exp": int((now + self.access_token_ttl).timestamp()),
        }
        return encode_jwt(payload, self.jwt_secret)

    def decode_access_token(self, token: str) -> dict:
        return decode_jwt(token, self.jwt_secret)

    def token_response(self, user: AuthUser) -> TokenResponse:
        expires_at = datetime.now(timezone.utc) + self.access_token_ttl
        return TokenResponse(access_token=self.issue_access_token(user), expires_at=expires_at)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "pbkdf2_sha256${iterations}${salt}${digest}".format(
        iterations=PASSWORD_HASH_ITERATIONS,
        salt=_b64url(salt),
        digest=_b64url(password_hash),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected_digest = encoded_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        _b64url_decode(salt),
        int(iterations),
    )
    return hmac.compare_digest(_b64url(digest), expected_digest)


def encode_jwt(payload: dict, secret: str) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    signing_input = f"{_json_b64(header)}.{_json_b64(payload)}"
    signature = _sign(signing_input, secret)
    return f"{signing_input}.{signature}"


def decode_jwt(token: str, secret: str) -> dict:
    try:
        header_segment, payload_segment, signature = token.split(".", 2)
    except ValueError as exc:
        raise InvalidCredentialsError("Malformed token") from exc
    signing_input = f"{header_segment}.{payload_segment}"
    expected_signature = _sign(signing_input, secret)
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidCredentialsError("Invalid token signature")

    header = json.loads(_b64url_decode(header_segment))
    if header.get("alg") != JWT_ALGORITHM:
        raise InvalidCredentialsError("Unsupported token algorithm")
    payload = json.loads(_b64url_decode(payload_segment))
    if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
        raise InvalidCredentialsError("Token expired")
    return payload


def build_thread_ownership_query(*, user_id: UUID, thread_id: str):
    return select(AgentThread).where(
        AgentThread.user_id == user_id,
        AgentThread.thread_id == thread_id,
    )


def _json_b64(payload: dict) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _sign(signing_input: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(digest)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(f"{encoded}{padding}".encode("ascii"))


_global_auth_service = AuthService(
    jwt_secret=os.getenv("EASYPLAN_JWT_SECRET") or os.getenv("JWT_SECRET_KEY", "dev-only-change-me"),
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _auth_user_from_model(user: User) -> AuthUser:
    return AuthUser(
        id=user.id,
        email=user.email,
        password_hash=user.password_hash or "",
        display_name=user.display_name,
        status=user.status,
    )


def get_auth_service() -> AuthService:
    return _global_auth_service


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DatabaseUserRepository:
    return DatabaseUserRepository(session)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    repository: Annotated[DatabaseUserRepository, Depends(get_user_repository)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    try:
        user = await auth_service.register_user(
            repository=repository,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered") from exc
    return auth_service.token_response(user)


@router.post("/token", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    repository: Annotated[DatabaseUserRepository, Depends(get_user_repository)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    try:
        user = await auth_service.authenticate_user(repository, payload.email, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from exc
    return auth_service.token_response(user)


async def get_current_user(
    repository: Annotated[DatabaseUserRepository, Depends(get_user_repository)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        claims = auth_service.decode_access_token(token)
        user = await repository.get_by_id(UUID(claims["sub"]))
    except (InvalidCredentialsError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return user
