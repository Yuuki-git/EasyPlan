import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4


TODOIST_AUTHORIZATION_URL = "https://app.todoist.com/oauth/authorize"
TODOIST_TOKEN_URL = "https://api.todoist.com/oauth/access_token"
DEFAULT_TODOIST_SCOPES = ["task:add"]


class OAuthStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthAuthorizationStart:
    provider: str
    authorization_url: str
    state: str
    expires_at: datetime


@dataclass
class OAuthStateRecord:
    user_id: UUID
    provider: str
    state: str
    redirect_uri: str
    requested_scopes: list[str]
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass
class IntegrationCredentialRecord:
    id: UUID
    user_id: UUID
    provider: str
    display_name: str
    auth_type: str
    encrypted_credentials: bytes
    scopes: list[str]
    token_expires_at: datetime | None
    status: str = "connected"
    external_account_id: str | None = None


class CredentialCipher:
    """Authenticated JSON envelope encryption abstraction for OAuth credentials."""

    def __init__(self, *, secret: str) -> None:
        if not secret:
            raise ValueError("CredentialCipher requires a non-empty secret")
        self._key = hashlib.sha256(secret.encode("utf-8")).digest()

    def encrypt_json(self, payload: dict[str, Any]) -> bytes:
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        nonce = secrets.token_bytes(16)
        ciphertext = _xor(plaintext, _keystream(self._key, nonce, len(plaintext)))
        mac = hmac.new(self._key, b"v1" + nonce + ciphertext, hashlib.sha256).digest()
        return b"v1." + base64.urlsafe_b64encode(nonce + ciphertext + mac)

    def decrypt_json(self, encrypted: bytes) -> dict[str, Any]:
        if not encrypted.startswith(b"v1."):
            raise ValueError("Unsupported credential envelope")
        raw = base64.urlsafe_b64decode(encrypted[3:])
        nonce, rest = raw[:16], raw[16:]
        ciphertext, mac = rest[:-32], rest[-32:]
        expected_mac = hmac.new(self._key, b"v1" + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("Credential envelope authentication failed")
        plaintext = _xor(ciphertext, _keystream(self._key, nonce, len(ciphertext)))
        return json.loads(plaintext.decode("utf-8"))


class InMemoryOAuthStateRepository:
    def __init__(self) -> None:
        self._states: dict[str, OAuthStateRecord] = {}

    def save(self, record: OAuthStateRecord) -> None:
        self._states[record.state] = record

    def get(self, state: str) -> OAuthStateRecord | None:
        return self._states.get(state)

    def consume(self, *, state: str, user_id: UUID | None, provider: str) -> OAuthStateRecord:
        record = self._states.get(state)
        if record is None:
            raise OAuthStateError("Unknown OAuth state")
        if record.provider != provider:
            raise OAuthStateError("OAuth state provider mismatch")
        if user_id is not None and record.user_id != user_id:
            raise OAuthStateError("OAuth state user mismatch")
        if record.consumed_at is not None:
            raise OAuthStateError("OAuth state already consumed")
        if record.expires_at < datetime.now(timezone.utc):
            raise OAuthStateError("OAuth state expired")
        record.consumed_at = datetime.now(timezone.utc)
        return record


class InMemoryIntegrationRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[UUID, str], IntegrationCredentialRecord] = {}

    def upsert(self, record: IntegrationCredentialRecord) -> IntegrationCredentialRecord:
        self._records[(record.user_id, record.provider)] = record
        return record

    def get(self, user_id: UUID, provider: str) -> IntegrationCredentialRecord | None:
        return self._records.get((user_id, provider))


class TodoistOAuthTokenClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        http_client: Any | None = None,
        token_url: str = TODOIST_TOKEN_URL,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.http_client = http_client
        self.token_url = token_url
        self.timeout_seconds = timeout_seconds

    async def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if self.http_client is not None:
            response = await self.http_client.post(self.token_url, data=data, timeout=self.timeout_seconds)
        else:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.post(self.token_url, data=data, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()


class TodoistOAuthService:
    def __init__(
        self,
        *,
        state_repository: InMemoryOAuthStateRepository,
        integration_repository: InMemoryIntegrationRepository,
        token_client: Any,
        cipher: CredentialCipher,
        client_id: str,
        client_secret: str,
        authorization_url: str = TODOIST_AUTHORIZATION_URL,
        scopes: list[str] | None = None,
    ) -> None:
        self.state_repository = state_repository
        self.integration_repository = integration_repository
        self.token_client = token_client
        self.cipher = cipher
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorization_url = authorization_url
        self.scopes = scopes or DEFAULT_TODOIST_SCOPES

    def start_authorization(
        self,
        *,
        user_id: UUID,
        redirect_uri: str,
    ) -> OAuthAuthorizationStart:
        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.state_repository.save(
            OAuthStateRecord(
                user_id=user_id,
                provider="todoist",
                state=state,
                redirect_uri=redirect_uri,
                requested_scopes=list(self.scopes),
                expires_at=expires_at,
            )
        )
        query = urlencode(
            {
                "client_id": self.client_id,
                "scope": ",".join(self.scopes),
                "state": state,
                "response_type": "code",
                "redirect_uri": redirect_uri,
            }
        )
        return OAuthAuthorizationStart(
            provider="todoist",
            authorization_url=f"{self.authorization_url}?{query}",
            state=state,
            expires_at=expires_at,
        )

    async def complete_callback(
        self,
        *,
        user_id: UUID | None,
        provider: str,
        code: str,
        state: str,
    ) -> IntegrationCredentialRecord:
        if provider != "todoist":
            raise OAuthStateError(f"Unsupported OAuth provider: {provider}")
        state_record = self.state_repository.consume(state=state, user_id=user_id, provider=provider)
        token_payload = await self.token_client.exchange_code(
            code=code,
            redirect_uri=state_record.redirect_uri,
        )
        expires_in = token_payload.get("expires_in")
        token_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in is not None else None
        )
        scopes = _parse_scopes(token_payload.get("scope")) or state_record.requested_scopes
        return self.integration_repository.upsert(
            IntegrationCredentialRecord(
                id=uuid4(),
                user_id=state_record.user_id,
                provider=provider,
                display_name="Todoist",
                auth_type="oauth2",
                encrypted_credentials=self.cipher.encrypt_json(token_payload),
                scopes=scopes,
                token_expires_at=token_expires_at,
            )
        )

    def get_state(self, state: str) -> OAuthStateRecord | None:
        return self.state_repository.get(state)


def build_default_todoist_oauth_service() -> TodoistOAuthService:
    client_id = os.getenv("TODOIST_CLIENT_ID", "todoist-client-id")
    client_secret = os.getenv("TODOIST_CLIENT_SECRET", "todoist-client-secret")
    token_client = TodoistOAuthTokenClient(client_id=client_id, client_secret=client_secret)
    return TodoistOAuthService(
        state_repository=InMemoryOAuthStateRepository(),
        integration_repository=InMemoryIntegrationRepository(),
        token_client=token_client,
        cipher=CredentialCipher(secret=os.getenv("EASYPLAN_CREDENTIAL_SECRET", "dev-only-credential-secret")),
        client_id=client_id,
        client_secret=client_secret,
    )


def _parse_scopes(scope: Any) -> list[str]:
    if isinstance(scope, str):
        separator = "," if "," in scope else " "
        return [item for item in (part.strip() for part in scope.split(separator)) if item]
    if isinstance(scope, list):
        return [str(item) for item in scope]
    return []


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        chunks.append(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:length]


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(left_byte ^ right_byte for left_byte, right_byte in zip(left, right))
