from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from deebee.remote_connections import RemoteProfile, encode_instruction, read_instruction
from deebee.workbench import DatabaseWorkbenches


def test_remote_profiles_validate_and_encrypt_private_key(tmp_path: Path) -> None:
    path = tmp_path / "connections.json"
    manager = DatabaseWorkbenches(store_path=path, defaults=[])
    ssh = manager.create_connection(
        {
            "driver": "ssh",
            "name": "Ops SSH",
            "host": "server.internal",
            "port": 22,
            "user": "operator",
            "password": "key-passphrase",
            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----",
            "options": {
                "auth_method": "private_key",
                "strict_host_key": True,
                "host_key_fingerprint": "SHA256:known",
            },
        }
    )
    rdp = manager.create_connection(
        {
            "driver": "rdp",
            "name": "Windows",
            "host": "windows.internal",
            "port": 3389,
            "user": "alice",
            "password": "rdp-secret",
            "options": {
                "domain": "ACME",
                "security": "nla",
                "ignore_certificate": True,
                "color_depth": 24,
                "timezone": "Australia/Perth",
            },
        }
    )

    stored = path.read_text(encoding="utf-8")
    assert "key-passphrase" not in stored
    assert "BEGIN OPENSSH PRIVATE KEY" not in stored
    assert "rdp-secret" not in stored
    assert ssh["driver"] == "ssh" and "private_key" not in ssh and "password" not in ssh
    assert rdp["options"]["domain"] == "ACME"

    restarted = DatabaseWorkbenches(store_path=path, defaults=[])
    profile = restarted.remote_profile(ssh["id"], "ssh")
    assert profile.password == "key-passphrase"
    assert "BEGIN OPENSSH PRIVATE KEY" in profile.private_key


@pytest.mark.parametrize(
    ("driver", "options", "message"),
    [
        ("ssh", {"auth_method": "agent"}, "SSH 认证方式无效"),
        ("rdp", {"security": "invalid"}, "RDP 安全模式无效"),
        ("rdp", {"color_depth": 8}, "RDP 色深仅支持"),
    ],
)
def test_remote_profile_rejects_invalid_options(
    tmp_path: Path, driver: str, options: dict[str, Any], message: str
) -> None:
    manager = DatabaseWorkbenches(store_path=tmp_path / "connections.json", defaults=[])
    with pytest.raises(Exception, match=message):
        manager.create_connection(
            {
                "driver": driver,
                "name": "Remote",
                "host": "server.internal",
                "port": 22 if driver == "ssh" else 3389,
                "user": "operator",
                "password": "secret",
                "options": options,
            }
        )


def test_guacamole_instruction_codec_handles_utf8_and_multiple_values() -> None:
    raw = encode_instruction("error", "认证失败", "512")

    async def decode() -> tuple[str, list[str], bytes]:
        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()
        return await read_instruction(reader)

    opcode, values, original = asyncio.run(decode())
    assert opcode == "error"
    assert values == ["认证失败", "512"]
    assert original == raw


def test_remote_profile_public_shape_never_exposes_secrets() -> None:
    profile = RemoteProfile(
        id="ssh-test",
        driver="ssh",
        name="SSH",
        host="127.0.0.1",
        port=22,
        user="root",
        password="password",
        private_key="private-key",
        options={"auth_method": "password"},
    )
    public = profile.public()
    assert "password" not in public
    assert "private_key" not in public
