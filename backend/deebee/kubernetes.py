from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import yaml

from .mysql import DeeBeeError

if TYPE_CHECKING:
    from .remote_connections import RemoteProfile


K8S_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")


def _kubectl() -> str:
    executable = shutil.which("kubectl")
    if not executable:
        raise DeeBeeError("Kubernetes 客户端不可用：服务端未安装 kubectl")
    return executable


def _server_url(profile: "RemoteProfile") -> str:
    raw = profile.host.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if not parsed.hostname:
        raise DeeBeeError("Kubernetes API Server 地址无效")
    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = parsed.port or profile.port
    except ValueError as exc:
        raise DeeBeeError("Kubernetes API Server 端口无效") from exc
    netloc = f"{hostname}:{port}" if port else hostname
    return urlunsplit((parsed.scheme or "https", netloc, parsed.path.rstrip("/"), "", ""))


def _generated_kubeconfig(profile: "RemoteProfile") -> str:
    if not profile.password:
        raise DeeBeeError("Kubernetes Bearer Token 不能为空")
    namespace = str(profile.options.get("namespace", "")).strip()
    cluster: dict[str, Any] = {"server": _server_url(profile)}
    if not profile.options.get("verify_tls", True):
        cluster["insecure-skip-tls-verify"] = True
    config = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "deebee", "cluster": cluster}],
        "contexts": [
            {
                "name": "deebee",
                "context": {
                    "cluster": "deebee",
                    "user": "deebee-user",
                    **({"namespace": namespace} if namespace else {}),
                },
            }
        ],
        "current-context": "deebee",
        "users": [{"name": "deebee-user", "user": {"token": profile.password}}],
    }
    # JSON is valid YAML and avoids quoting mistakes in bearer tokens.
    return json.dumps(config, ensure_ascii=False)


def kubeconfig_text(profile: "RemoteProfile") -> str:
    auth_method = str(profile.options.get("k8s_auth_method", "token"))
    if auth_method == "kubeconfig":
        value = profile.private_key.strip()
        if not value:
            raise DeeBeeError("Kubeconfig 不能为空")
        if len(value.encode("utf-8")) > 1024 * 1024:
            raise DeeBeeError("Kubeconfig 不能超过 1 MiB")
        try:
            parsed = yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise DeeBeeError("Kubeconfig 格式无效") from exc
        if not isinstance(parsed, dict) or not all(
            key in parsed for key in ("apiVersion", "clusters", "contexts")
        ):
            raise DeeBeeError("Kubeconfig 格式无效")
        for cluster in parsed.get("clusters", []):
            cluster_config = cluster.get("cluster", {}) if isinstance(cluster, dict) else {}
            if isinstance(cluster_config, dict) and "certificate-authority" in cluster_config:
                raise DeeBeeError("Kubeconfig 必须内嵌 CA，不能引用服务端文件")
        for user in parsed.get("users", []):
            user_config = user.get("user", {}) if isinstance(user, dict) else {}
            if not isinstance(user_config, dict):
                continue
            if "exec" in user_config or "auth-provider" in user_config:
                raise DeeBeeError("Kubeconfig 不允许执行外部认证插件")
            if any(key in user_config for key in ("client-certificate", "client-key", "tokenFile")):
                raise DeeBeeError("Kubeconfig 凭据必须内嵌，不能引用服务端文件")
        return value
    return _generated_kubeconfig(profile)


@contextlib.contextmanager
def temporary_kubeconfig(profile: "RemoteProfile"):
    descriptor, name = tempfile.mkstemp(prefix="deebee-kubeconfig-", suffix=".yaml")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(kubeconfig_text(profile))
            handle.flush()
            os.fsync(handle.fileno())
        namespace = str(profile.options.get("namespace", "")).strip()
        if namespace:
            result = subprocess.run(
                [_kubectl(), "--kubeconfig", name, "config", "set-context", "--current", "--namespace", namespace],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode:
                raise DeeBeeError("无法设置 Kubeconfig 默认 Namespace")
        yield name
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)


def _redacted_error(profile: "RemoteProfile", value: str, config_path: str) -> str:
    text = value.replace(config_path, "[kubeconfig]")
    if profile.password:
        text = text.replace(profile.password, "[REDACTED]")
    return text.strip()[:2000]


def run_kubectl(
    profile: "RemoteProfile",
    config_path: str,
    args: list[str],
    *,
    timeout: int = 20,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "KUBECONFIG": config_path}
    try:
        result = subprocess.run(
            [_kubectl(), "--request-timeout=15s", *args],
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeeBeeError("Kubernetes 请求超时") from exc
    if check and result.returncode:
        detail = _redacted_error(profile, result.stderr or result.stdout, config_path)
        raise DeeBeeError(f"Kubernetes 请求失败：{detail or 'kubectl 执行失败'}")
    return result


def test_kubernetes(profile: "RemoteProfile") -> dict[str, Any]:
    started = time.perf_counter()
    with temporary_kubeconfig(profile) as config_path:
        version_result = run_kubectl(profile, config_path, ["get", "--raw", "/version"])
        try:
            version = json.loads(version_result.stdout)
        except json.JSONDecodeError as exc:
            raise DeeBeeError("Kubernetes API 返回了无效的版本信息") from exc
        access = run_kubectl(
            profile,
            config_path,
            ["auth", "can-i", "get", "pods", "--all-namespaces"],
            check=False,
        )
        if access.stdout.strip().lower() not in {"yes", "no"}:
            detail = _redacted_error(profile, access.stderr or access.stdout, config_path)
            raise DeeBeeError(f"Kubernetes 权限检查失败：{detail}")
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "version": str(version.get("gitVersion", "Kubernetes")),
            "database": "Pod 可见" if access.stdout.strip().lower() == "yes" else "连接成功",
        }


def _json_output(profile: "RemoteProfile", config_path: str, args: list[str]) -> dict[str, Any]:
    result = run_kubectl(profile, config_path, [*args, "-o", "json"])
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeeBeeError("Kubernetes API 返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise DeeBeeError("Kubernetes API 返回格式无效")
    return value


def _service_account_namespace(token: str) -> str:
    """Use the namespace claim as JumpServer's restricted-token fallback does."""
    parts = token.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return ""
    namespace = claims.get("kubernetes.io/serviceaccount/namespace", "")
    return str(namespace).strip() if namespace else ""


def _configured_namespace(profile: "RemoteProfile", config_path: str) -> str:
    configured = str(profile.options.get("namespace", "")).strip()
    if configured:
        return configured
    result = run_kubectl(
        profile,
        config_path,
        ["config", "view", "--minify", "-o", "jsonpath={..namespace}"],
        check=False,
    )
    current = result.stdout.strip()
    if current:
        return current
    return _service_account_namespace(profile.password)


def kubernetes_tree(profile: "RemoteProfile") -> list[dict[str, Any]]:
    """Mirror JumpServer's namespace -> pod -> regular-container tree."""
    with temporary_kubeconfig(profile) as config_path:
        namespace = _configured_namespace(profile, config_path)
        if namespace:
            pod_data = _json_output(profile, config_path, ["get", "pods", "-n", namespace])
            namespace_names = [namespace]
        else:
            namespace_result = run_kubectl(
                profile, config_path, ["get", "namespaces", "-o", "json"], check=False
            )
            if namespace_result.returncode:
                raise DeeBeeError(
                    "无法确定可访问的 Namespace；请在连接设置中填写默认 Namespace"
                )
            try:
                namespace_data = json.loads(namespace_result.stdout)
            except json.JSONDecodeError as exc:
                raise DeeBeeError("Kubernetes API 返回了无效 JSON") from exc
            namespace_names = sorted(
                str(item.get("metadata", {}).get("name", ""))
                for item in namespace_data.get("items", [])
                if item.get("metadata", {}).get("name")
            )
            all_pods = run_kubectl(
                profile,
                config_path,
                ["get", "pods", "--all-namespaces", "-o", "json"],
                check=False,
            )
            if all_pods.returncode == 0:
                try:
                    pod_data = json.loads(all_pods.stdout)
                except json.JSONDecodeError as exc:
                    raise DeeBeeError("Kubernetes API 返回了无效 JSON") from exc
            else:
                pod_data = {"items": []}
                for namespace_name in namespace_names:
                    result = run_kubectl(
                        profile,
                        config_path,
                        ["get", "pods", "-n", namespace_name, "-o", "json"],
                        check=False,
                    )
                    if result.returncode:
                        continue
                    try:
                        value = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        continue
                    pod_data["items"].extend(value.get("items", []))

    namespaces: dict[str, dict[str, Any]] = {
        name: {"name": name, "type": "namespace", "pods": []}
        for name in namespace_names
    }
    for raw_pod in pod_data.get("items", []):
        metadata = raw_pod.get("metadata", {})
        spec = raw_pod.get("spec", {})
        ns_name = str(metadata.get("namespace", namespace or "default"))
        pod_name = str(metadata.get("name", ""))
        if not pod_name:
            continue
        target = namespaces.setdefault(
            ns_name, {"name": ns_name, "type": "namespace", "pods": []}
        )
        containers = sorted(
            {
                str(item.get("name", ""))
                for item in spec.get("containers", [])
                if item.get("name")
            }
        )
        target["pods"].append(
            {
                "name": pod_name,
                "type": "pod",
                "containers": [
                    {"name": item, "type": "container"} for item in containers
                ],
            }
        )
    for item in namespaces.values():
        item["pods"].sort(key=lambda value: value["name"])
    return sorted(namespaces.values(), key=lambda value: value["name"])


def validate_target(namespace: str, pod: str, container: str) -> None:
    values = (("Namespace", namespace), ("Pod", pod), ("Container", container))
    if not all(value and len(value) <= 253 and K8S_NAME.fullmatch(value) for _, value in values):
        invalid = next(
            label
            for label, value in values
            if not value or len(value) > 253 or not K8S_NAME.fullmatch(value)
        )
        raise DeeBeeError(f"{invalid} 名称无效")


def find_container_shell(
    profile: "RemoteProfile", config_path: str, namespace: str, pod: str, container: str
) -> str:
    base = ["exec", "-n", namespace, pod, "-c", container, "--"]
    probe = run_kubectl(
        profile,
        config_path,
        [*base, "sh", "-c", "command -v bash || command -v sh"],
        check=False,
    )
    shell = probe.stdout.strip().splitlines()[-1:] or []
    if probe.returncode == 0 and shell:
        return shell[0]
    for candidate, command in (
        ("powershell", ["powershell", "-NoProfile", "-Command", "Get-Command powershell"]),
        ("cmd", ["cmd", "/c", "where cmd"]),
    ):
        if run_kubectl(profile, config_path, [*base, *command], check=False).returncode == 0:
            return candidate
    raise DeeBeeError("容器中未找到 bash、sh、powershell 或 cmd")


@dataclass
class KubernetesTerminalProcess:
    id: str
    profile: "RemoteProfile"
    config_path: str
    namespace: str = ""
    pod: str = ""
    container: str = ""
    master_fd: int = -1
    process: asyncio.subprocess.Process | None = None
    proxy_process: asyncio.subprocess.Process | None = None
    session_dir: str = ""
    reader_task: asyncio.Task[Any] | None = None
    input_archive: str = ""
    output_archive: str = ""
    sensitive_input: bool = False
    started: float = field(default_factory=time.monotonic)

    async def start(
        self,
        cols: int,
        rows: int,
        sender: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        import pty

        master, slave = pty.openpty()
        self.master_fd = master
        self.resize(cols, rows)
        env = {
            **os.environ,
            "KUBECONFIG": self.config_path,
            "TERM": "xterm-256color",
            "PS1": f"{self.profile.name}# ",
            "HISTFILE": "/dev/null",
        }
        if self.container:
            validate_target(self.namespace, self.pod, self.container)
            shell = await asyncio.to_thread(
                find_container_shell,
                self.profile,
                self.config_path,
                self.namespace,
                self.pod,
                self.container,
            )
            command = [
                _kubectl(),
                "exec",
                "-it",
                "-n",
                self.namespace,
                self.pod,
                "-c",
                self.container,
                "--",
                shell,
            ]
            preexec_fn = None
        else:
            command, env, preexec_fn = await self._cluster_shell(env)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                start_new_session=True,
                preexec_fn=preexec_fn,
            )
        finally:
            os.close(slave)

        async def pump() -> None:
            loop = asyncio.get_running_loop()
            try:
                while True:
                    try:
                        raw = await loop.run_in_executor(None, os.read, master, 32768)
                    except OSError:
                        break
                    if not raw:
                        break
                    text = raw.decode("utf-8", errors="replace")
                    if len(self.output_archive.encode("utf-8")) < 4096:
                        self.output_archive += text[:4096]
                    prompt_tail = text[-160:].lower()
                    if any(
                        marker in prompt_tail
                        for marker in ("password:", "passphrase:", "token:", "secret:")
                    ):
                        self.sensitive_input = True
                    await sender({"type": "data", "id": self.id, "data": text})
            finally:
                code = await self.process.wait() if self.process else 0
                await sender({"type": "closed", "id": self.id, "exit_code": code})

        self.reader_task = asyncio.create_task(pump())
        await sender({"type": "opened", "id": self.id})

    async def _cluster_shell(
        self, env: dict[str, str]
    ) -> tuple[list[str], dict[str, str], Callable[[], None] | None]:
        """Expose an uncredentialed local kubectl endpoint, as JumpServer does."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        proxy_env = {**os.environ, "KUBECONFIG": self.config_path}
        self.proxy_process = await asyncio.create_subprocess_exec(
            _kubectl(),
            "proxy",
            "--address=127.0.0.1",
            f"--port={port}",
            "--api-prefix=/",
            "--disable-filter=true",
            env=proxy_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            if self.proxy_process.returncode is not None:
                raise DeeBeeError("无法启动 Kubernetes 凭据隔离代理")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.05)
            else:
                writer.close()
                await writer.wait_closed()
                break
        else:
            raise DeeBeeError("Kubernetes 凭据隔离代理启动超时")

        self.session_dir = tempfile.mkdtemp(prefix="deebee-k8s-shell-")
        namespace = str(self.profile.options.get("namespace", "")).strip()
        public_config = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {"name": "deebee-proxy", "cluster": {"server": f"http://127.0.0.1:{port}"}}
            ],
            "contexts": [
                {
                    "name": "deebee-proxy",
                    "context": {
                        "cluster": "deebee-proxy",
                        "user": "deebee-session",
                        **({"namespace": namespace} if namespace else {}),
                    },
                }
            ],
            "current-context": "deebee-proxy",
            "users": [{"name": "deebee-session", "user": {}}],
        }
        public_path = Path(self.session_dir, "config")
        public_path.write_text(json.dumps(public_config), encoding="utf-8")
        os.chmod(public_path, 0o600)
        preexec_fn: Callable[[], None] | None = None
        if os.geteuid() == 0:
            nobody_uid = 65534
            nobody_gid = 65534
            os.chown(self.session_dir, nobody_uid, nobody_gid)
            os.chown(public_path, nobody_uid, nobody_gid)

            def drop_privileges() -> None:
                os.setgroups([])
                os.setgid(nobody_gid)
                os.setuid(nobody_uid)

            preexec_fn = drop_privileges
        shell_env = {
            **env,
            "HOME": self.session_dir,
            "KUBECONFIG": str(public_path),
        }
        return ["/bin/bash", "--noprofile", "--norc"], shell_env, preexec_fn

    def write(self, data: str) -> None:
        if self.master_fd < 0:
            return
        os.write(self.master_fd, data.encode("utf-8"))
        if len(self.input_archive.encode("utf-8")) < 4096:
            self.input_archive += "[REDACTED]" if self.sensitive_input else data[:4096]
        if self.sensitive_input and ("\n" in data or "\r" in data):
            self.sensitive_input = False

    def resize(self, cols: int, rows: int) -> None:
        if self.master_fd < 0:
            return
        import fcntl
        import termios

        cols = max(20, min(500, int(cols)))
        rows = max(5, min(200, int(rows)))
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
        if self.reader_task and self.reader_task is not asyncio.current_task():
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader_task
        if self.master_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(self.master_fd)
            self.master_fd = -1
        if self.proxy_process and self.proxy_process.returncode is None:
            self.proxy_process.terminate()
            try:
                await asyncio.wait_for(self.proxy_process.wait(), timeout=2)
            except TimeoutError:
                self.proxy_process.kill()
                with contextlib.suppress(Exception):
                    await self.proxy_process.wait()
        if self.session_dir:
            shutil.rmtree(self.session_dir, ignore_errors=True)
            self.session_dir = ""
