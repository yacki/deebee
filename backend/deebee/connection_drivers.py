from __future__ import annotations

import ssl
import time
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

try:
    import clickhouse_connect
except ModuleNotFoundError:  # pragma: no cover - exercised by packaged dependency checks.
    clickhouse_connect = None  # type: ignore[assignment]

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover - exercised by packaged dependency checks.
    redis = None  # type: ignore[assignment]

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ModuleNotFoundError:  # pragma: no cover - exercised by packaged dependency checks.
    MongoClient = None  # type: ignore[assignment,misc]
    PyMongoError = Exception  # type: ignore[assignment,misc]

from .mysql import DeeBeeError


@dataclass(frozen=True)
class ConnectionProfile:
    id: str
    name: str
    host: str
    port: int
    user: str
    password: str
    default_database: str = ""
    default_schema: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    transport: dict[str, Any] = field(default_factory=dict)

    driver: ClassVar[str] = ""

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("password")
        value.pop("transport")
        value["driver"] = self.driver
        return value


@dataclass(frozen=True)
class RedisProfile(ConnectionProfile):
    driver: ClassVar[str] = "redis"


@dataclass(frozen=True)
class ClickHouseProfile(ConnectionProfile):
    driver: ClassVar[str] = "clickhouse"


@dataclass(frozen=True)
class MongoDBProfile(ConnectionProfile):
    driver: ClassVar[str] = "mongodb"


class ConnectionOnlyWorkbench:
    """Connection testing for drivers whose object workbench lands in a later phase."""

    profile_type: type[ConnectionProfile] = ConnectionProfile
    dependency_name = "数据库"

    def __init__(self) -> None:
        self.profiles: dict[str, ConnectionProfile] = {}
        self.sessions: dict[str, Any] = {}

    def require_profile(self, profile_id: str) -> ConnectionProfile:
        profile = self.profiles.get(profile_id)
        if not profile:
            raise DeeBeeError("连接不存在")
        return profile

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.public() for profile in self.profiles.values()]

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        return self.test_connection(self.require_profile(profile_id))

    def test_connection(self, profile: ConnectionProfile) -> dict[str, Any]:
        raise NotImplementedError

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def cleanup_workspace(self, workspace_id: str) -> int:
        return 0

    def databases(self, profile_id: str) -> list[dict[str, Any]]:
        self.require_profile(profile_id)
        raise DeeBeeError(f"{self.dependency_name} 工作台将在下一阶段开放；当前已支持连接测试")


class RedisWorkbench(ConnectionOnlyWorkbench):
    profile_type = RedisProfile
    dependency_name = "Redis"

    def test_connection(self, profile: ConnectionProfile) -> dict[str, Any]:
        if redis is None:
            raise DeeBeeError("Redis 驱动未安装，请安装 redis")
        started = time.perf_counter()
        database = int(profile.default_database or "0")
        from .transports import transport_manager

        host, port = transport_manager.endpoint(
            profile.id, profile.host, profile.port, profile.transport
        )
        try:
            client = redis.Redis(
                host=host,
                port=port,
                username=profile.user or None,
                password=profile.password or None,
                db=database,
                ssl=bool(profile.options.get("tls", False)),
                ssl_cert_reqs=(
                    ssl.CERT_REQUIRED
                    if profile.options.get("verify_tls", True)
                    else ssl.CERT_NONE
                ),
                socket_connect_timeout=10,
                socket_timeout=10,
                decode_responses=True,
            )
            try:
                client.ping()
                details: dict[str, Any] = {}
                try:
                    info = client.info("server")
                    details = {
                        "version": info.get("redis_version", ""),
                        "mode": info.get("redis_mode", ""),
                    }
                except redis.RedisError:
                    # A restricted ACL may permit PING but not INFO; PING is sufficient.
                    pass
                return {
                    "ok": True,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "database": f"db{database}",
                    **details,
                }
            finally:
                client.close()
        except redis.RedisError as exc:
            raise DeeBeeError(str(exc), type(exc).__name__) from exc
        except (OSError, ValueError) as exc:
            raise DeeBeeError(str(exc), type(exc).__name__) from exc


class ClickHouseWorkbench(ConnectionOnlyWorkbench):
    profile_type = ClickHouseProfile
    dependency_name = "ClickHouse"

    def test_connection(self, profile: ConnectionProfile) -> dict[str, Any]:
        if clickhouse_connect is None:
            raise DeeBeeError("ClickHouse 驱动未安装，请安装 clickhouse-connect")
        started = time.perf_counter()
        client = None
        from .transports import transport_manager

        host, port = transport_manager.endpoint(
            profile.id, profile.host, profile.port, profile.transport
        )
        try:
            client = clickhouse_connect.get_client(
                host=host,
                port=port,
                username=profile.user or "default",
                password=profile.password,
                database=profile.default_database or "default",
                secure=bool(profile.options.get("tls", False)),
                verify=bool(profile.options.get("verify_tls", True)),
                connect_timeout=10,
                send_receive_timeout=10,
            )
            result = client.query("SELECT version(), currentUser(), currentDatabase()")
            row = result.result_rows[0]
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "version": str(row[0]),
                "account": str(row[1]),
                "database": str(row[2]),
            }
        except Exception as exc:
            code = getattr(exc, "code", None) or getattr(exc, "error_code", None)
            raise DeeBeeError(str(exc), code or type(exc).__name__) from exc
        finally:
            if client is not None:
                client.close()


class MongoDBWorkbench(ConnectionOnlyWorkbench):
    profile_type = MongoDBProfile
    dependency_name = "MongoDB"

    def test_connection(self, profile: ConnectionProfile) -> dict[str, Any]:
        if MongoClient is None:
            raise DeeBeeError("MongoDB 驱动未安装，请安装 pymongo")
        started = time.perf_counter()
        options = profile.options
        from .transports import transport_manager

        host, port = transport_manager.endpoint(
            profile.id, profile.host, profile.port, profile.transport
        )
        parameters: dict[str, Any] = {
            "host": host,
            "port": port,
            "serverSelectionTimeoutMS": 10000,
            "connectTimeoutMS": 10000,
            "appname": "DeeBee",
            "tls": bool(options.get("tls", False)),
            "directConnection": bool(
                options.get("direct_connection", False)
                or profile.transport.get("ssh_tunnel")
                or profile.transport.get("proxy_enabled")
            ),
        }
        if options.get("tls"):
            parameters["tlsAllowInvalidCertificates"] = not bool(
                options.get("verify_tls", True)
            )
        if profile.user:
            parameters.update(
                username=profile.user,
                password=profile.password,
                authSource=str(options.get("auth_database") or "admin"),
            )
        client = None
        try:
            client = MongoClient(**parameters)
            client.admin.command("ping")
            details: dict[str, Any] = {}
            try:
                build = client.admin.command("buildInfo")
                details["version"] = str(build.get("version", ""))
            except PyMongoError:
                # Application users may be allowed to ping but not inspect build info.
                pass
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "database": profile.default_database or "admin",
                "topology": client.topology_description.topology_type_name,
                **details,
            }
        except PyMongoError as exc:
            raise DeeBeeError(str(exc), getattr(exc, "code", None)) from exc
        except (OSError, ValueError) as exc:
            raise DeeBeeError(str(exc), type(exc).__name__) from exc
        finally:
            if client is not None:
                client.close()


redis_workbench = RedisWorkbench()
clickhouse_workbench = ClickHouseWorkbench()
mongodb_workbench = MongoDBWorkbench()
