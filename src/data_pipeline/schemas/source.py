"""Source configuration models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    API = "api"
    DATABASE = "database"
    FILE = "file"


class AuthType(str, Enum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BASIC = "basic"
    NONE = "none"


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    NEEDS_AUTH = "needs_auth"
    FAILED = "failed"
    UNTESTED = "untested"


class OAuthConfig(BaseModel):
    client_id: str
    authorization_url: str
    token_url: str
    scopes: list[str] = Field(default_factory=list)
    device_authorization_url: str | None = None


class APIConfig(BaseModel):
    base_url: str
    auth_type: AuthType = AuthType.API_KEY
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    rate_limit_rpm: int | None = None
    pagination_style: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)



class SourceConfig(BaseModel):
    id: str
    name: str
    slug: str
    source_type: SourceType
    provider: str
    enabled: bool = True
    connection_status: ConnectionStatus = ConnectionStatus.UNTESTED
    default_endpoint: str | None = None
    oauth: OAuthConfig | None = None
    api: APIConfig | None = None
    icon: str | None = None
    tagline: str | None = None
    last_sync_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InferredConfig(BaseModel):
    """Typed model returned by ProviderRegistry.infer_config().

    Replaces the old dict[str, Any] return value for type safety downstream.
    """

    name: str
    provider: str
    base_url: str
    auth_type: str
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    pagination_style: str = "unknown"
    api_style: str = "rest"
    rate_limit_rpm: int | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)
    health_endpoint: str = "/"
    docs_url: str | None = None
    default_endpoints: dict[str, str] = Field(default_factory=dict)
    source: str = "inferred"
