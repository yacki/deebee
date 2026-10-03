import json
import os
from types import SimpleNamespace

import pytest

from deebee.kubernetes import (
    _service_account_namespace,
    kubeconfig_text,
    kubernetes_tree,
    temporary_kubeconfig,
    validate_target,
)
from deebee.mysql import DeeBeeError
from deebee.remote_connections import RemoteProfile


def profile(**changes):
    values = {
        "id": "k8s-test",
        "driver": "k8s",
        "name": "test-cluster",
        "host": "cluster.example.test",
        "port": 6443,
        "user": "",
        "password": "secret-token",
        "private_key": "",
        "options": {
            "k8s_auth_method": "token",
            "namespace": "team-a",
            "verify_tls": False,
        },
    }
    values.update(changes)
    return RemoteProfile(**values)


def test_token_kubeconfig_is_well_formed_and_scoped():
    value = json.loads(kubeconfig_text(profile()))
    assert value["clusters"][0]["cluster"] == {
        "server": "https://cluster.example.test:6443",
        "insecure-skip-tls-verify": True,
    }
    assert value["contexts"][0]["context"]["namespace"] == "team-a"
    assert value["users"][0]["user"]["token"] == "secret-token"


def test_full_kubeconfig_is_written_with_private_permissions():
    raw = "apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\n"
    target = profile(
        password="",
        private_key=raw,
        options={"k8s_auth_method": "kubeconfig", "namespace": "", "verify_tls": True},
    )
    with temporary_kubeconfig(target) as path:
        assert open(path, encoding="utf-8").read() == raw.strip()
        assert os.stat(path).st_mode & 0o777 == 0o600
    assert not os.path.exists(path)


def test_json_kubeconfig_is_accepted():
    raw = json.dumps({"apiVersion": "v1", "kind": "Config", "clusters": [], "contexts": []})
    target = profile(
        password="",
        private_key=raw,
        options={"k8s_auth_method": "kubeconfig", "namespace": "", "verify_tls": True},
    )
    assert kubeconfig_text(target) == raw


@pytest.mark.parametrize(
    "unsafe_user",
    [
        {"exec": {"command": "/bin/sh"}},
        {"auth-provider": {"name": "unsafe"}},
        {"client-key": "/etc/passwd"},
        {"client-certificate": "/etc/passwd"},
        {"tokenFile": "/etc/passwd"},
    ],
)
def test_kubeconfig_rejects_plugins_and_server_file_references(unsafe_user):
    raw = json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [],
            "contexts": [],
            "users": [{"name": "unsafe", "user": unsafe_user}],
        }
    )
    target = profile(
        password="",
        private_key=raw,
        options={"k8s_auth_method": "kubeconfig", "namespace": "", "verify_tls": True},
    )
    with pytest.raises(DeeBeeError):
        kubeconfig_text(target)


def test_service_account_namespace_fallback():
    import base64

    claims = base64.urlsafe_b64encode(
        json.dumps({"kubernetes.io/serviceaccount/namespace": "team-a"}).encode()
    ).decode().rstrip("=")
    assert _service_account_namespace(f"header.{claims}.signature") == "team-a"
    assert _service_account_namespace("opaque-token") == ""


def test_resource_tree_matches_jumpserver_namespace_pod_container_shape(monkeypatch):
    namespaces = {"items": [{"metadata": {"name": "z-team"}}, {"metadata": {"name": "a-team"}}]}
    pods = {
        "items": [
            {
                "metadata": {"namespace": "a-team", "name": "web-2"},
                "spec": {
                    "containers": [{"name": "sidecar"}, {"name": "app"}],
                    "initContainers": [{"name": "migration"}],
                },
            }
        ]
    }

    def fake_run(_, __, args, **___):
        if "config" in args:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return SimpleNamespace(
            stdout=json.dumps(pods if "pods" in args else namespaces), stderr="", returncode=0
        )

    monkeypatch.setattr("deebee.kubernetes.run_kubectl", fake_run)
    result = kubernetes_tree(profile(options={"k8s_auth_method": "token", "namespace": "", "verify_tls": False}))
    assert [item["name"] for item in result] == ["a-team", "z-team"]
    assert result[0]["pods"][0]["name"] == "web-2"
    assert [item["name"] for item in result[0]["pods"][0]["containers"]] == ["app", "sidecar"]


@pytest.mark.parametrize(
    "target",
    [
        ("default", "api-0", "app"),
        ("team.prod", "api-server-7d9c", "sidecar-2"),
    ],
)
def test_terminal_target_validation_accepts_kubernetes_names(target):
    validate_target(*target)


@pytest.mark.parametrize(
    "target",
    [
        ("default", "../../tmp", "app"),
        ("default", "api-0", "--container"),
        ("", "api-0", "app"),
        ("default", "p" * 254, "app"),
    ],
)
def test_terminal_target_validation_rejects_unsafe_names(target):
    with pytest.raises(DeeBeeError):
        validate_target(*target)
