from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def base_path() -> str:
    """Return a normalized URL prefix, or an empty string for root hosting."""
    value = os.getenv("DEEBEE_BASE_PATH", "/deebee").strip()
    if value in {"", "/"}:
        return ""
    return "/" + value.strip("/")


def persistent_secret(connections_file: Path) -> str:
    """Return an explicit secret or create a stable one beside persisted data."""
    configured = os.getenv("DEEBEE_TOKEN_SECRET", "").strip()
    if configured:
        return configured

    secret_file = Path(
        os.getenv(
            "DEEBEE_SECRET_FILE",
            str(connections_file.parent / "secret.key"),
        )
    ).expanduser()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = secret_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(48)
        try:
            file_descriptor = os.open(
                secret_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            value = secret_file.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(value + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    if not value:
        raise RuntimeError(f"DeeBee 密钥文件为空：{secret_file}")
    return value


_connections_file = Path(
    os.getenv(
        "DEEBEE_CONNECTIONS_FILE",
        str(Path(__file__).resolve().parents[1] / "data" / "connections.json"),
    )
).expanduser()


@dataclass(frozen=True)
class Settings:
    base_path: str = base_path()
    admin_user: str = os.getenv("DEEBEE_ADMIN_USER", "admin")
    admin_password: str = os.getenv("DEEBEE_ADMIN_PASSWORD", "deebee")
    token_secret: str = persistent_secret(_connections_file)
    connections_file: Path = _connections_file
    cors_origins: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv(
            "DEEBEE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if item.strip()
    )
    mysql_name: str = os.getenv("DEEBEE_MYSQL_NAME", "MySQL")
    mysql_host: str = os.getenv("DEEBEE_MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("DEEBEE_MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("DEEBEE_MYSQL_USER", "root")
    mysql_password: str = os.getenv("DEEBEE_MYSQL_PASSWORD", "")
    mysql_database: str = os.getenv("DEEBEE_MYSQL_DATABASE", "")
    mysql_enabled: bool = env_bool("DEEBEE_MYSQL_ENABLED", False)
    postgres_enabled: bool = env_bool("DEEBEE_POSTGRES_ENABLED", False)
    postgres_name: str = os.getenv("DEEBEE_POSTGRES_NAME", "PostgreSQL")
    postgres_host: str = os.getenv("DEEBEE_POSTGRES_HOST", "127.0.0.1")
    postgres_port: int = int(os.getenv("DEEBEE_POSTGRES_PORT", "5432"))
    postgres_user: str = os.getenv("DEEBEE_POSTGRES_USER", "postgres")
    postgres_password: str = os.getenv("DEEBEE_POSTGRES_PASSWORD", "")
    postgres_database: str = os.getenv("DEEBEE_POSTGRES_DATABASE", "postgres")
    postgres_schema: str = os.getenv("DEEBEE_POSTGRES_SCHEMA", "public")
    guacd_host: str = os.getenv("DEEBEE_GUACD_HOST", "127.0.0.1")
    guacd_port: int = int(os.getenv("DEEBEE_GUACD_PORT", "4822"))


settings = Settings()
