from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JsonDict, utc_created_at, utc_updated_at, uuid_pk


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_integrations_user_provider"),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="connected")
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_credentials: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credential_version: Mapped[int] = mapped_column(nullable=False, default=1)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    token_expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()
    last_used_at: Mapped[datetime | None]


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    code_verifier_hash: Mapped[str | None] = mapped_column(String(256))
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    created_at: Mapped[datetime] = utc_created_at()
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    transport: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(Text)
    command_template: Mapped[JsonDict | None] = mapped_column(JSONB)
    auth_scheme: Mapped[str] = mapped_column(String(64), nullable=False, default="bearer")
    required_headers: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    timeout_ms: Mapped[int] = mapped_column(nullable=False, default=10_000)
    max_connections: Mapped[int] = mapped_column(nullable=False, default=10)
    allowed_hosts: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trust_level: Mapped[str] = mapped_column(String(64), nullable=False, default="approved")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()


class McpTool(Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
    )

    id: Mapped[UUID] = uuid_pk()
    server_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    input_schema: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[JsonDict | None] = mapped_column(JSONB)
    annotations: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    version_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    discovered_at: Mapped[datetime] = utc_created_at()
    last_seen_at: Mapped[datetime] = utc_created_at()
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
