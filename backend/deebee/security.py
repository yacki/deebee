from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .config import settings


TOKEN_TTL = 12 * 60 * 60


def issue_token(username: str) -> str:
    payload = json.dumps(
        {"sub": username, "exp": int(time.time()) + TOKEN_TTL}, separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(settings.token_secret.encode(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def verify_token(token: str) -> str | None:
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(settings.token_secret.encode(), encoded.encode(), hashlib.sha256).digest()
        supplied_bytes = base64.urlsafe_b64decode(supplied + "=" * (-len(supplied) % 4))
        if not hmac.compare_digest(expected, supplied_bytes):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if int(payload["exp"]) < int(time.time()):
            return None
        return str(payload["sub"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
