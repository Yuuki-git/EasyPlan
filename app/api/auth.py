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

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models.thread import AgentThread


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


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users_by_email: dict[str, AuthUser] = {}
        self._users_by_id: dict[UUID, AuthUser] = {}

    def add(self, user: AuthUser) -> None:
        if user.email in self._users_by_email:
            raise DuplicateEmailError(user.email)
        self._users_by_email[user.email] = user
        self._users_by_id[user.id] = user

    def get_by_email(self, email: str) -> AuthUser | None:
        return self._users_by_email.get(normalize_email(email))

    def get_by_id(self, user_id: UUID) -> AuthUser | None:
        return self._users_by_id.get(user_id)


class AuthService:
    def __init__(
        self,
        *,
        user_repository: InMemoryUserRepository,
        jwt_secret: str,
        access_token_ttl: timedelta = timedelta(minutes=DEFAULT_ACCESS_TOKEN_TTL_MINUTES),
    ) -> None:
        self.user_repository = user_repository
        self.jwt_secret = jwt_secret
        self.access_token_ttl = access_token_ttl

    def register_user(
        self,
        email: str,
        password: str,
        display_name: str | None = None,
    ) -> AuthUser:
        normalized_email = normalize_email(email)
        if self.user_repository.get_by_email(normalized_email) is not None:
            raise DuplicateEmailError(normalized_email)
        user = AuthUser(
            id=uuid4(),
            email=normalized_email,
            password_hash=hash_password(password),
            display_name=display_name,
        )
        self.user_repository.add(user)
        return user

    def authenticate_user(self, email: str, password: str) -> AuthUser:
        user = self.user_repository.get_by_email(email)
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


_global_user_repository = InMemoryUserRepository()
_global_auth_service = AuthService(
    user_repository=_global_user_repository,
    jwt_secret=os.getenv("EASYPLAN_JWT_SECRET", "dev-only-change-me"),
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest) -> TokenResponse:
    try:
        user = _global_auth_service.register_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered") from exc
    return _global_auth_service.token_response(user)


@router.post("/token", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    try:
        user = _global_auth_service.authenticate_user(payload.email, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from exc
    return _global_auth_service.token_response(user)


async def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        claims = _global_auth_service.decode_access_token(token)
        user = _global_user_repository.get_by_id(UUID(claims["sub"]))
    except (InvalidCredentialsError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return user
