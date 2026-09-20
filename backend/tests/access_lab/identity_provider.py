"""Loopback-only identity protocol fixture. Never deploy this application publicly."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse


app = FastAPI()
ISSUER = "http://127.0.0.1:18991"
AUDIENCE = "http://127.0.0.1:18080"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True) | {"kid": "lab-key-1", "use": "sig", "alg": "RS256"}
SCOPES = "deebee:access resources:read ssh:exec db:query db:write privilege:use"
codes = {}
opaque = {}
revoked = set()


def claims(subject="lab_subject", **changes):
    return {"iss": ISSUER, "sub": subject, "aud": AUDIENCE, "iat": int(time.time()), "exp": int(time.time()) + 600,
            "scope": SCOPES, "client_id": "lab-agent", **changes}


@app.get("/.well-known/openid-configuration")
def discovery():
    return {"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks", "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token",
            "introspection_endpoint": ISSUER + "/introspect", "response_types_supported": ["code"], "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"]}


@app.get("/jwks")
def jwks():
    return {"keys": [JWK]}


@app.post("/fixture/token")
async def fixture_token(request: Request):
    body = await request.json()
    payload = claims(**body.get("claims", {}))
    if body.get("opaque"):
        token = "lab_opaque_" + secrets.token_hex(16)
        opaque[token] = payload
    else:
        token = jwt.encode(payload, KEY, algorithm="RS256", headers={"kid": "lab-key-1", "typ": body.get("typ", "at+jwt")})
    return {"access_token": token}


@app.post("/verify-key")
async def verify_key(request: Request):
    if request.headers.get("authorization") != "Bearer lab_verifier_service_secret":
        raise HTTPException(401)
    body = await request.json()
    key = body.get("api_key")
    if key != "lab_external_key_test_only" or key in revoked:
        return {"active": False}
    from datetime import datetime, timezone
    return {"active": True, "subject": "lab_subject", "credential_id": "lab_external_key", "audience": "deebee",
            "expires_at": datetime.fromtimestamp(time.time() + 600, timezone.utc).isoformat(), "client_id": "lab-agent", "scopes": SCOPES.split()}


@app.post("/introspect")
async def introspect(request: Request):
    expected = "Basic " + base64.b64encode(b"deebee-lab:lab_verifier_service_secret").decode()
    if request.headers.get("authorization") != expected:
        raise HTTPException(401)
    body = await request.form()
    token = body.get("token", "")
    payload = opaque.get(token)
    return {"active": True, "token_type": "Bearer", **payload} if payload and token not in revoked else {"active": False}


@app.post("/fixture/revoke")
async def revoke(request: Request):
    body = await request.json()
    revoked.add(body["token"])
    return {"revoked": True}


@app.post("/fixture/reset")
def reset():
    revoked.clear()
    return {"reset": True}


@app.get("/authorize")
def authorize(client_id: str, redirect_uri: str, state: str, nonce: str, code_challenge: str, code_challenge_method: str):
    if client_id != "deebee-browser" or not redirect_uri.startswith(AUDIENCE + "/api/auth/oidc/") or code_challenge_method != "S256":
        raise HTTPException(400)
    code = secrets.token_urlsafe(24)
    codes[code] = {"nonce": nonce, "challenge": code_challenge, "redirect_uri": redirect_uri, "created": time.time()}
    return RedirectResponse(redirect_uri + "?" + urlencode({"code": code, "state": state}))


@app.post("/token")
async def token(request: Request):
    form = await request.form()
    record = codes.pop(form.get("code", ""), None)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
    if not record or record["challenge"] != challenge or record["redirect_uri"] != form.get("redirect_uri") or record["created"] + 300 < time.time():
        raise HTTPException(400)
    return {"token_type": "Bearer", "expires_in": 600,
            "access_token": jwt.encode(claims(), KEY, algorithm="RS256", headers={"kid": "lab-key-1", "typ": "at+jwt"}),
            "id_token": jwt.encode(claims(aud="deebee-browser", nonce=record["nonce"]), KEY, algorithm="RS256", headers={"kid": "lab-key-1", "typ": "JWT"})}
