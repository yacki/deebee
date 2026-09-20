from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import math
import os
import secrets
import socket
import time
from collections import defaultdict, deque
from datetime import datetime
from urllib.parse import urlsplit

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from .models import AccessError, AuthContext, SCOPES
from .store import AccessStore


PASSWORDS = PasswordHasher()


class Authenticator:
    def __init__(self, store: AccessStore):
        self.store = store
        self.cache: dict[tuple, tuple[float, dict]] = {}
        self.rates: dict[str, deque] = defaultdict(deque)

    def rate(self, key: str, limit: int = 120):
        now = time.monotonic()
        bucket = self.rates[key]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= limit:
            raise AccessError("RATE_LIMITED", "请求过于频繁，请稍后再试", 429)
        bucket.append(now)
        if len(self.rates) > 10000:
            self.rates = defaultdict(deque, {k: v for k, v in self.rates.items() if v and v[-1] > now - 60})

    async def validate_url(self, url: str, source: dict | None = None):
        parsed = urlsplit(url)
        dev = os.getenv("DEEBEE_ACCESS_ALLOW_INSECURE_LOCAL", "") == "1"
        if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
            raise AccessError("INVALID_IDENTITY_URL", "身份服务 URL 不合法")
        if parsed.scheme != "https" and not (dev and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise AccessError("INVALID_IDENTITY_URL", "身份源必须使用 HTTPS（仅显式测试模式允许 loopback HTTP）")
        if source:
            bases = [source.get("issuer", ""), source.get("verify_endpoint", ""), source.get("introspection_endpoint", ""), *source.get("trusted_origins", [])]
            origins = {(urlsplit(base).scheme, urlsplit(base).netloc) for base in bases if base}
            if (parsed.scheme, parsed.netloc) not in origins:
                raise AccessError("UNTRUSTED_IDENTITY_ENDPOINT", "发现的身份服务地址不在信任列表")
        try:
            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise AccessError("IDENTITY_PROVIDER_UNAVAILABLE", "身份源地址无法解析", 503) from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                raise AccessError("INVALID_IDENTITY_URL", "身份服务不得指向链路本地或元数据地址")
            if not ip.is_global and not (dev and ip.is_loopback) and os.getenv("DEEBEE_ACCESS_ALLOW_PRIVATE_IDP", "") != "1":
                raise AccessError("INVALID_IDENTITY_URL", "内网身份源需部署管理员显式启用 DEEBEE_ACCESS_ALLOW_PRIVATE_IDP")

    async def http_json(self, url: str, source: dict, *, method: str = "GET", **kwargs) -> dict:
        await self.validate_url(url, source)
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, url, **kwargs) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 262144:
                            raise ValueError("identity response too large")
                        chunks.append(chunk)
                    import json
                    result = json.loads(b"".join(chunks))
                    if not isinstance(result, dict):
                        raise ValueError("identity response must be object")
                    return result
        except (httpx.HTTPError, ValueError) as exc:
            raise AccessError("IDENTITY_PROVIDER_UNAVAILABLE", "身份源验证失败或不可用", 503) from exc

    async def discovery(self, source: dict, refresh: bool = False) -> dict:
        key = (source["id"], source["version"], "discovery")
        cached = self.cache.get(key)
        if cached and cached[0] > time.time() and not refresh:
            return cached[1]
        url = source["issuer"].rstrip("/") + "/.well-known/openid-configuration"
        data = await self.http_json(url, source)
        if data.get("issuer") != source["issuer"]:
            raise AccessError("INVALID_ISSUER", "OIDC 发现结果的 issuer 不匹配", 401)
        self.cache[key] = (time.time() + 300, data)
        return data

    async def jwks(self, source: dict, refresh: bool = False) -> dict:
        key = (source["id"], source["version"], "jwks")
        cached = self.cache.get(key)
        if cached and cached[0] > time.time() and not refresh:
            return cached[1]
        self.rate("jwks:" + source["id"], 10)
        metadata = await self.discovery(source)
        if not metadata.get("jwks_uri"):
            raise AccessError("INVALID_IDENTITY_SOURCE", "OIDC 缺少 JWKS 地址", 503)
        data = await self.http_json(metadata["jwks_uri"], source)
        if not isinstance(data.get("keys"), list):
            raise AccessError("INVALID_IDENTITY_SOURCE", "JWKS 格式无效", 503)
        self.cache[key] = (time.time() + 300, data)
        return data

    async def decode_jwt(self, token: str, source: dict, *, id_token: bool = False, nonce: str = "") -> dict:
        try:
            header = jwt.get_unverified_header(token)
            if not id_token and (not isinstance(header.get("typ"), str) or header["typ"].lower() != "at+jwt"):
                raise AccessError("INVALID_TOKEN_TYPE", "请使用 RFC 9068 Access Token，不能使用 ID Token", 401)
            if header.get("alg") not in source.get("allowed_algorithms", ["RS256"]):
                raise AccessError("INVALID_TOKEN", "Token 算法不被允许", 401)
            keys = (await self.jwks(source))["keys"]
            key = next((k for k in keys if k.get("kid") == header.get("kid") and k.get("use", "sig") == "sig"), None)
            if key is None:
                keys = (await self.jwks(source, refresh=True))["keys"]
                key = next((k for k in keys if k.get("kid") == header.get("kid") and k.get("use", "sig") == "sig"), None)
            if key is None:
                raise AccessError("INVALID_TOKEN", "Token 签名密钥未知", 401)
            payload = jwt.decode(token, jwt.PyJWK.from_dict(key).key,
                                 algorithms=source.get("allowed_algorithms", ["RS256"]),
                                 audience=source["client_id"] if id_token else source["audiences"], issuer=source["issuer"],
                                 options={"require": ["iss", "sub", "aud", "exp", "iat"]}, leeway=5)
            if id_token and (not nonce or payload.get("nonce") != nonce):
                raise AccessError("INVALID_NONCE", "OIDC 登录状态无效", 401)
            if id_token and len(payload["aud"] if isinstance(payload["aud"], list) else [payload["aud"]]) > 1 and payload.get("azp") != source["client_id"]:
                raise AccessError("INVALID_TOKEN", "OIDC azp 不匹配", 401)
            return payload
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError) as exc:
            raise AccessError("INVALID_TOKEN", "Token 无效或已过期", 401) from exc

    def bind(self, source: dict, claims: dict, credential_id: str, method: str, headers: dict,
             resource_ids: list | None = None) -> AuthContext:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AccessError("INVALID_TOKEN", "身份凭据缺少可信主体", 401)
        aud = claims.get("aud", [])
        aud = [aud] if isinstance(aud, str) else aud
        if not isinstance(aud, list) or not all(isinstance(a, str) for a in aud):
            raise AccessError("INVALID_TOKEN", "身份凭据受众格式无效", 401)
        if source.get("audiences") and not set(aud).intersection(source["audiences"]):
            raise AccessError("INVALID_AUDIENCE", "身份凭据受众不匹配", 401)
        try:
            expires = float(claims["exp"])
        except (ValueError, TypeError, KeyError) as exc:
            raise AccessError("INVALID_TOKEN", "身份凭据缺少有效期", 401) from exc
        if not math.isfinite(expires) or expires <= time.time():
            raise AccessError("TOKEN_EXPIRED", "身份凭据已到期", 401)
        scope = claims.get("scope", [])
        if not isinstance(scope, (str, list)) or (isinstance(scope, list) and not all(isinstance(s, str) for s in scope)):
            raise AccessError("INVALID_TOKEN", "身份凭据 scope 格式无效", 401)
        scopes = frozenset(scope.split() if isinstance(scope, str) else scope)
        if not set(source.get("required_scopes", [])).issubset(scopes):
            raise AccessError("INSUFFICIENT_SCOPE", "身份凭据 scope 不足", 403)
        client_id = claims.get("client_id", "")
        if source.get("allowed_client_ids") and client_id not in source["allowed_client_ids"]:
            raise AccessError("INVALID_CLIENT", "该客户端未获允许", 403)
        binding = next((b for b in self.store.list("bindings") if b["source_id"] == source["id"] and b["subject"] == subject), None)
        if binding is None:
            raise AccessError("IDENTITY_UNMAPPED", "此身份尚未映射到 DeeBee 内部身份", 403)
        principal = self.store.get("principals", binding["principal_id"])
        if not source.get("enabled") or not binding.get("enabled") or not principal.get("enabled"):
            raise AccessError("IDENTITY_DISABLED", "身份源、绑定或内部身份已停用", 403)
        return AuthContext(principal["id"], source["id"], subject, binding["id"], credential_id,
                           method, scopes & SCOPES, expires, str(client_id), tuple(resource_ids or []), headers)

    async def authenticate(self, headers: dict[str, str], *, session_token: str = "", allow_session: bool = False) -> AuthContext:
        key = headers.get("x-deebee-api-key", "")
        bearer = headers.get("authorization", "")
        if (key and bearer) or (allow_session and session_token and (key or bearer)):
            raise AccessError("AMBIGUOUS_CREDENTIALS", "请只提交一种身份凭据", 400)
        if len(key) > 8192 or len(bearer) > 16384:
            raise AccessError("INVALID_TOKEN", "身份凭据过长", 401)
        # Some Streamable HTTP clients only support Authorization: Bearer.
        # This opt-in compatibility path still validates the managed key's
        # digest, binding, scopes, resource caps, expiry and revocation state.
        if (not key and os.getenv("DEEBEE_ACCESS_ALLOW_MANAGED_KEY_BEARER", "") == "1"
                and bearer.startswith("Bearer ")):
            candidate = bearer[7:].strip()
            if candidate.startswith("dbk_"):
                key, bearer = candidate, ""
        source_id = headers.get("x-deebee-identity-source", "")
        if key:
            if key.startswith("dbk_"):
                parts = key.split("_", 2)
                record = self.store.get("keys", parts[1] if len(parts) == 3 else "", required=False)
                if not record or not secrets.compare_digest(record["digest"], self.store.digest(key)):
                    raise AccessError("UNAUTHENTICATED", "API-Key 无效", 401)
                if not record["enabled"]:
                    raise AccessError("IDENTITY_DISABLED", "API-Key 已撤销", 403)
                if source_id and record["source_id"] != source_id:
                    raise AccessError("INVALID_IDENTITY_SOURCE", "API-Key 身份源不匹配", 401)
                source = self.store.get("sources", record["source_id"])
                if source.get("validation_mode") != "managed":
                    raise AccessError("INVALID_IDENTITY_SOURCE", "API-Key 验证方式不匹配", 401)
                return self.bind(source, {"sub": record["subject"], "exp": record["expires_at"], "scope": record["scopes"]},
                                 record["id"], "api_key", headers, record.get("resource_ids"))
            if not source_id:
                raise AccessError("IDENTITY_SOURCE_REQUIRED", "外部 API-Key 需要身份源选择器", 401)
            source = self.enabled_source(source_id, "api_key")
            if source["validation_mode"] != "external_http":
                raise AccessError("INVALID_IDENTITY_SOURCE", "该来源不支持外部 Key", 401)
            credentials = self.store.secret(source["credential_ref"]) if source.get("credential_ref") else {}
            verify_headers = {"Authorization": "Bearer " + credentials["service_secret"]} if credentials.get("service_secret") else {}
            data = await self.http_json(source["verify_endpoint"], source, method="POST", headers=verify_headers,
                                        json={"api_key": key, "audience": source["audiences"][0], "request_id": secrets.token_hex(12)})
            for target, path in source.get("field_mapping", {}).items():
                value = data
                for segment in path.split("."):
                    value = value.get(segment) if isinstance(value, dict) else None
                data[target] = value
            if data.get("active") is not True or not data.get("credential_id") or not isinstance(data.get("scopes"), list):
                raise AccessError("UNAUTHENTICATED", "外部 API-Key 无效", 401)
            try:
                expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
                if expires_at.tzinfo is None:
                    raise ValueError("timezone required")
                expiry = expires_at.timestamp()
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise AccessError("INVALID_TOKEN", "外部 API-Key 有效期无效", 401) from exc
            return self.bind(source, {"sub": data.get("subject"), "aud": data.get("audience"), "exp": expiry,
                                     "scope": data["scopes"], "client_id": data.get("client_id", "")},
                             str(data["credential_id"]), "external_api_key", headers)
        if bearer:
            if not bearer.startswith("Bearer "):
                raise AccessError("UNAUTHENTICATED", "需要 Bearer Access Token", 401)
            token = bearer[7:].strip()
            if not source_id:
                try:
                    issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
                except jwt.PyJWTError as exc:
                    raise AccessError("IDENTITY_SOURCE_REQUIRED", "Opaque Token 需要身份源选择器", 401) from exc
                sources = [s for s in self.store.list("sources") if s.get("type") == "oidc" and s.get("issuer") == issuer]
                if len(sources) != 1:
                    raise AccessError("INVALID_ISSUER", "Token 身份源未受信任或需明确选择", 401)
                source_id = sources[0]["id"]
            source = self.enabled_source(source_id, "oidc")
            if source["validation_mode"] == "jwt":
                claims = await self.decode_jwt(token, source)
            else:
                secret = self.store.secret(source["credential_ref"]) if source.get("credential_ref") else {}
                claims = await self.http_json(source["introspection_endpoint"], source, method="POST",
                                             data={"token": token, "token_type_hint": "access_token"},
                                             auth=(source.get("service_client_id", ""), secret.get("service_secret", "")))
                if claims.get("active") is not True or claims.get("iss", source["issuer"]) != source["issuer"]:
                    raise AccessError("INVALID_TOKEN", "Token 无效", 401)
                if not isinstance(claims.get("token_type", "Bearer"), str) or claims.get("token_type", "Bearer").lower() != "bearer":
                    raise AccessError("INVALID_TOKEN_TYPE", "Token 类型不支持", 401)
            return self.bind(source, claims, hashlib.sha256(token.encode()).hexdigest()[:24], "oidc", headers)
        if allow_session and session_token:
            return self.local_session(session_token)
        raise AccessError("UNAUTHENTICATED", "请提供 API-Key 或 Access Token", 401)

    def enabled_source(self, source_id: str, kind: str) -> dict:
        source = self.store.get("sources", source_id, required=False)
        if not source or source.get("type") != kind:
            raise AccessError("INVALID_IDENTITY_SOURCE", "身份源不受信任", 401)
        if not source.get("enabled"):
            raise AccessError("IDENTITY_DISABLED", "身份源已停用", 403)
        return source

    def login(self, username: str, password: str, ip: str) -> tuple[str, str]:
        self.rate("login-ip:" + ip, 15)
        self.rate("login-user:" + username, 10)
        principal = next((p for p in self.store.list("principals") if p.get("username") == username and p.get("password_hash")), None)
        try:
            if principal is None:
                # Fixed dummy hash prevents a fast nonexistent-user path.
                PASSWORDS.verify(PASSWORDS.hash(secrets.token_hex(16)), password)
            else:
                PASSWORDS.verify(principal["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            raise AccessError("UNAUTHENTICATED", "用户名或密码错误", 401)
        if principal is None or not principal.get("enabled"):
            raise AccessError("UNAUTHENTICATED", "用户名或密码错误", 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO local_sessions VALUES(?,?,?,?)", (self.store.digest(token), principal["id"], time.time() + 3600, csrf))
            self.store.audit(principal["id"], "local.login", principal["id"])
        return token, csrf

    def local_session(self, token: str) -> AuthContext:
        with self.store.lock:
            row = self.store.db.execute("SELECT * FROM local_sessions WHERE digest=?", (self.store.digest(token),)).fetchone()
        if not row or row["expires_at"] <= time.time():
            raise AccessError("UNAUTHENTICATED", "本地会话已失效", 401)
        principal = self.store.get("principals", row["principal_id"])
        if not principal.get("enabled"):
            raise AccessError("IDENTITY_DISABLED", "本地身份已停用", 403)
        return AuthContext(principal["id"], "local", principal["id"], "local:" + principal["id"],
                           self.store.digest(token), "local", frozenset(SCOPES), row["expires_at"], headers={"local_session": token})

    def csrf(self, token: str, value: str):
        with self.store.lock:
            row = self.store.db.execute("SELECT csrf FROM local_sessions WHERE digest=?", (self.store.digest(token),)).fetchone()
        if not row or not value or not secrets.compare_digest(row["csrf"], value):
            raise AccessError("CSRF_REJECTED", "会话校验失败，请重新登录", 403)

    async def revalidate(self, ctx: AuthContext) -> AuthContext:
        if ctx.method == "local":
            return self.local_session(ctx.headers["local_session"])
        return await self.authenticate(ctx.headers)
