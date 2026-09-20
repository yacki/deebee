from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCOPES = {"resources:read", "ssh:exec", "db:query", "db:write", "privilege:use"}
KINDS = {"principals", "sources", "bindings", "resources", "accounts", "grants", "keys"}


class AccessError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Principal(Model):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["human", "service"] = "human"
    enabled: bool = True
    username: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=1024)


class Source(Model):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["api_key", "oidc"]
    validation_mode: Literal["managed", "external_http", "jwt", "introspection"]
    enabled: bool = False
    issuer: str = ""
    audiences: list[str] = Field(default_factory=list, max_length=10)
    verify_endpoint: str = ""
    introspection_endpoint: str = ""
    allowed_algorithms: list[Literal["RS256", "ES256"]] = Field(default_factory=lambda: ["RS256"])
    required_scopes: list[str] = Field(default_factory=list)
    allowed_client_ids: list[str] = Field(default_factory=list)
    service_secret: str = Field(default="", max_length=4096)
    service_client_id: str = ""
    browser_login: bool = False
    client_id: str = ""
    client_secret: str = Field(default="", max_length=4096)
    # Additional origins must be explicitly authorized by the local administrator.
    trusted_origins: list[str] = Field(default_factory=list)
    field_mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_mode(self):
        valid = {"api_key": {"managed", "external_http"}, "oidc": {"jwt", "introspection"}}
        if self.validation_mode not in valid[self.type]:
            raise ValueError("身份验证服务类型与验证方式不匹配")
        if self.type == "oidc" and (not self.issuer or not self.audiences):
            raise ValueError("OIDC 需要 issuer 和 audience")
        if self.validation_mode == "external_http" and (not self.verify_endpoint or not self.audiences):
            raise ValueError("外部 API-Key 需要验证地址和 audience")
        if self.validation_mode == "introspection" and not self.introspection_endpoint:
            raise ValueError("在线验证需要 introspection 地址")
        if self.browser_login and (self.type != "oidc" or not self.client_id):
            raise ValueError("浏览器 OIDC 登录需要 client_id")
        return self


class Binding(Model):
    source_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=255)
    principal_id: str = Field(min_length=1)
    enabled: bool = True


class Resource(Model):
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(default="", max_length=40)
    project_groups: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=50)
    type: Literal["ssh", "mysql", "postgresql", "mssql", "redis", "clickhouse", "mongodb", "rdp"]
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(default="", max_length=128)
    schemas: list[str] = Field(default_factory=lambda: ["public"])
    host_key: str = ""
    host_key_algorithm: Literal["ssh-ed25519", "ecdsa-sha2-nistp256", "rsa-sha2-512", "rsa-sha2-256"] = "ssh-ed25519"
    tls: bool = True
    ca_file: str = ""
    enabled: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_output_bytes: int = Field(default=1048576, ge=1024, le=2097152)
    max_rows: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def boundary(self):
        self.environment = self.environment.strip()
        for field in ("project_groups", "tags"):
            values = list(dict.fromkeys(v.strip() for v in getattr(self, field) if v.strip()))
            if any(len(v) > 64 for v in values):
                raise ValueError("项目组和标签每项不能超过 64 个字符")
            setattr(self, field, values)
        if self.enabled and self.type not in {"ssh", "mysql", "postgresql"}:
            raise ValueError("此连接类型可加入资源管理，但暂不支持 MCP 操作")
        if self.enabled and self.type in {"mysql", "postgresql"} and not self.database:
            raise ValueError("数据库资源必须指定一个 database")
        if self.type == "postgresql" and not self.schemas:
            raise ValueError("PostgreSQL 至少指定一个 schema")
        if self.type == "ssh" and self.enabled and not self.host_key.startswith("SHA256:"):
            raise ValueError("启用 SSH 资源前必须核验并填写主机指纹")
        return self


class Account(Model):
    resource_id: str
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=255)
    tier: Literal["normal", "privileged"] = "normal"
    auth_method: Literal["password", "private_key"] = "password"
    password: str = Field(default="", max_length=4096)
    private_key: str = Field(default="", max_length=65536)
    passphrase: str = Field(default="", max_length=4096)
    enabled: bool = False
    permission_confirmed: bool = False
    permission_note: str = Field(default="", max_length=2000)


class Limits(Model):
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_rows: int = Field(default=1000, ge=1, le=10000)
    max_output_bytes: int = Field(default=1048576, ge=1024, le=2097152)


class Grant(Model):
    principal_id: str
    resource_id: str
    normal_account_id: str = ""
    privileged_account_id: str = ""
    allow_privileged: bool = False
    enabled: bool = True
    expires_at: float | None = None
    actions: list[str] = Field(default_factory=lambda: sorted(SCOPES))
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def scope_values(self):
        if set(self.actions) - SCOPES:
            raise ValueError("不支持的授权动作")
        if self.allow_privileged and not self.privileged_account_id:
            raise ValueError("允许特权前需要选择特权账号")
        return self


class KeyCreate(Model):
    name: str = Field(min_length=1, max_length=100)
    principal_id: str
    source_id: str = "local_keys"
    expires_in_days: int = Field(default=90, ge=1, le=365)
    scopes: list[str] = Field(default_factory=lambda: sorted(SCOPES - {"privilege:use", "db:write"}))
    resource_ids: list[str] = Field(default_factory=list)


class ExecutionInput(Model):
    resource_id: str
    mode: Literal["normal", "privileged"] = "normal"
    command: str = Field(default="", max_length=16384)
    sql: str = Field(default="", max_length=65536)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_rows: int = Field(default=1000, ge=1, le=10000)
    idempotency_key: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class AuthContext:
    principal_id: str
    source_id: str
    subject: str
    binding_id: str
    credential_id: str
    method: str
    scopes: frozenset[str]
    expires_at: float
    client_id: str = ""
    resource_ids: tuple[str, ...] = ()
    # Volatile request credentials, never serialized or written to audit/executions.
    headers: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if key != "headers"} | {
            "scopes": sorted(self.scopes), "resource_ids": list(self.resource_ids)
        }


ENTITY_MODELS = {"principals": Principal, "sources": Source, "bindings": Binding,
                 "resources": Resource, "accounts": Account, "grants": Grant}
