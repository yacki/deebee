from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode


PREVIEW_BYTES = 1024
CAPTURE_BYTES = 65536
REDACTED = "[REDACTED]"

# These names are deliberately exact. Broad matches such as ``key`` or
# ``credential`` would hide legitimate database columns from execution output.
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "client_secret",
    "cookie",
    "current_password",
    "id_token",
    "new_password",
    "passphrase",
    "password",
    "private_key",
    "proxy_password",
    "refresh_token",
    "service_secret",
    "set_cookie",
    "ssh_password",
    "ssh_private_key",
    "token",
    "access_token",
    "x_deebee_api_key",
}


def normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def sanitize(value: Any, key: object = "") -> Any:
    if normalized_key(key) in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {str(k): sanitize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


_JSON_SECRET = re.compile(
    r'(?i)("(?:' + "|".join(re.escape(k) for k in sorted(SENSITIVE_KEYS)) + r')"\s*:\s*)"(?:\\.|[^"\\])*(?:"|$)'
)
_FORM_SECRET = re.compile(
    r'(?i)(\b(?:' + "|".join(re.escape(k) for k in sorted(SENSITIVE_KEYS)) + r')=)[^&\s]*'
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PEM = re.compile(r"-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?(?:-----END [^-]*(?:PRIVATE KEY|CERTIFICATE)-----|$)", re.DOTALL)
_CLI_SECRET = re.compile(r"(?i)(\b(?:MYSQL_PWD|PGPASSWORD|--password|--passphrase|--token)(?:=|\s+))\S+")
_SHORT_PASSWORD = re.compile(r"(?i)(?<!\w)-p[^\s]+")
_SQL_SECRET = re.compile(r"(?is)(\b(?:IDENTIFIED\s+BY|PASSWORD\s*(?:=|TO)?|SECRET\s*=)\s*)(?:'[^']*'|\"[^\"]*\"|\S+)")


def redact_text(value: str) -> str:
    value = _PEM.sub(REDACTED, value)
    value = _JSON_SECRET.sub(lambda match: match.group(1) + json.dumps(REDACTED), value)
    value = _FORM_SECRET.sub(lambda match: match.group(1) + REDACTED, value)
    value = _CLI_SECRET.sub(lambda match: match.group(1) + REDACTED, value)
    value = _SHORT_PASSWORD.sub("-p" + REDACTED, value)
    value = _SQL_SECRET.sub(lambda match: match.group(1) + REDACTED, value)
    return _BEARER.sub("Bearer " + REDACTED, value)


def truncate_utf8(value: str, limit: int = PREVIEW_BYTES) -> tuple[str, bool]:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return value, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def preview_value(value: Any, *, already_truncated: bool = False) -> tuple[str, bool]:
    try:
        text = json.dumps(sanitize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        text = redact_text(str(value))
    result, truncated = truncate_utf8(text)
    return result, truncated or already_truncated


def preview_bytes(
    raw: bytes,
    content_type: str = "",
    *,
    total_bytes: int | None = None,
    already_truncated: bool = False,
) -> tuple[str, bool]:
    content_type = content_type.split(";", 1)[0].strip().lower()
    total = len(raw) if total_bytes is None else total_bytes
    capture_truncated = already_truncated or total > len(raw)
    if not raw:
        return "", capture_truncated
    if content_type.startswith("multipart/"):
        return f"<multipart payload: {total} bytes; file contents not archived>", capture_truncated
    if content_type and not (
        content_type.startswith("text/")
        or content_type in {"application/json", "application/problem+json", "application/x-www-form-urlencoded", "application/sql"}
        or content_type.endswith("+json")
    ):
        return f"<binary payload: {content_type}; {total} bytes>", capture_truncated
    text = raw.decode("utf-8", errors="replace")
    if content_type == "application/x-www-form-urlencoded" and not capture_truncated:
        try:
            form = [(key, REDACTED if normalized_key(key) in SENSITIVE_KEYS else value) for key, value in parse_qsl(text, keep_blank_values=True)]
            text = urlencode(form)
        except ValueError:
            text = redact_text(text)
    elif (content_type.endswith("json") or content_type.endswith("+json") or text.lstrip().startswith(("{", "["))) and not capture_truncated:
        try:
            text = json.dumps(sanitize(json.loads(text)), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError, ValueError):
            text = redact_text(text)
    else:
        text = redact_text(text)
    result, truncated = truncate_utf8(text)
    return result, truncated or capture_truncated


def parse_json(raw: bytes) -> Any | None:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
