"""Bounded maintenance protocols; no user-supplied shell or Kubernetes API paths."""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import ssl
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import Field, model_validator

from .models import AccessError, Model


class OperationInput(Model):
    resource_id: str = Field(min_length=1)
    mode: Literal["normal", "privileged"] = "normal"
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SSHInspectInput(OperationInput):
    check: Literal["overview", "disk", "memory", "processes", "network"] = "overview"


# Fixed commands intentionally omit arbitrary paths, pipes supplied by callers,
# environment interpolation and service mutations.
SSH_CHECKS = {
    "overview": "uname -sr; uptime; df -PT; free -m",
    "disk": "df -PT; df -Pi",
    "memory": "free -m; cat /proc/meminfo",
    "processes": "if ps -eo pid,ppid,comm,pcpu,pmem 2>/dev/null; then :; else ps -o pid,ppid,comm; fi",
    "network": "if command -v ss >/dev/null 2>&1; then ss -s && ss -lnt; else netstat -lnt; fi",
}


class KubernetesInput(OperationInput):
    operation: Literal["list", "get", "logs", "rollout", "restart"] = "list"
    kind: Literal["pods", "deployments", "events", "services", "replicasets"] = "pods"
    namespace: str = Field(default="", max_length=63, pattern=r"^$|^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
    name: str = Field(default="", max_length=253, pattern=r"^$|^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
    container: str = Field(default="", max_length=63, pattern=r"^$|^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
    tail_lines: int = Field(default=100, ge=1, le=1000)
    previous: bool = False

    @model_validator(mode="after")
    def boundary(self):
        if self.operation != "list" and not self.name:
            raise ValueError("get/logs/restart 必须指定资源名称")
        if self.operation == "logs" and self.kind != "pods":
            raise ValueError("logs 仅支持 pods")
        if self.operation in {"restart", "rollout"} and self.kind != "deployments":
            raise ValueError("restart/rollout 仅支持 deployments")
        if self.operation == "list" and self.name:
            raise ValueError("list 不接受名称；单对象请用 get")
        if self.operation != "logs" and (self.container or self.previous):
            raise ValueError("container/previous 仅用于 logs")
        return self


def namespace_for(resource: dict, requested: str) -> str:
    allowed = resource.get("namespaces", [])
    namespace = requested or (allowed[0] if len(allowed) == 1 else "")
    if not namespace or namespace not in allowed:
        raise AccessError("NAMESPACE_DENIED", "请明确选择获准 namespace", 403)
    return namespace


async def kube_request(resource, secret, method, path, *, params=None, body=None, limit=1048576):
    host = resource["host"]
    if any(c in host for c in "/?#@"):
        raise AccessError("INVALID_ARGUMENT", "K8s host 必须是主机名或 IP，不含 URL 路径")
    if not resource["tls"] and os.getenv("DEEBEE_ACCESS_ALLOW_INSECURE_LOCAL") != "1":
        raise AccessError("TLS_REQUIRED", "Kubernetes 必须启用 TLS")
    verify = ssl.create_default_context(cafile=resource.get("ca_file") or None)
    host = f"[{host}]" if ":" in host else host
    url = f"{'https' if resource['tls'] else 'http'}://{host}:{resource['port']}{path}"
    headers = {"Authorization": "Bearer " + secret.get("password", "")}
    if method == "PATCH":
        headers["Content-Type"] = "application/strategic-merge-patch+json"
    async with httpx.AsyncClient(verify=verify, timeout=20, trust_env=False, follow_redirects=False) as client:
        async with client.stream(method, url, headers=headers, params=params, json=body) as response:
            if response.status_code >= 300:
                raise AccessError("K8S_REQUEST_FAILED", f"Kubernetes API HTTP {response.status_code}", 403 if response.status_code == 403 else 502)
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise AccessError("OUTPUT_LIMIT", "Kubernetes 返回超过输出限制，请缩小查询范围", 413)
                chunks.append(chunk)
            content = b"".join(chunks).decode("utf-8", errors="replace")
            return content if path.endswith("/log") else json.loads(content)


async def kubernetes_execute(resource, account, secret, body, limit, handle):
    namespace = namespace_for(resource, body["namespace"])
    prefix = "/apis/apps/v1" if body["kind"] in {"deployments", "replicasets"} else "/api/v1"
    path = f"{prefix}/namespaces/{quote(namespace, safe='')}/{body['kind']}"
    if body["name"]:
        path += "/" + quote(body["name"], safe="")
    method, payload, params = "GET", None, None
    if body["operation"] == "logs":
        path += "/log"
        params = {"tailLines": body["tail_lines"], "limitBytes": limit, "previous": str(body["previous"]).lower()}
        if body["container"]:
            params["container"] = body["container"]
    elif body["operation"] == "list":
        params = {"limit": min(resource["max_rows"], 100)}
    elif body["operation"] == "restart":
        method = "PATCH"
        payload = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}}}}}
    async def observe():
        while True:
            result = await kube_request(resource, secret, method, path, params=params, body=payload, limit=limit)
            if body["operation"] != "rollout":
                return result
            status = result.get("status", {})
            desired = result.get("spec", {}).get("replicas", 1)
            if (status.get("observedGeneration", 0) >= result.get("metadata", {}).get("generation", 1)
                    and all(status.get(field, 0) == desired for field in ("replicas", "updatedReplicas", "availableReplicas"))
                    and not status.get("unavailableReplicas", 0)):
                return result
            # The execution manager owns the overall deadline and cancellation.
            await asyncio.sleep(.5)
    task = asyncio.create_task(observe())
    async def stop():
        task.cancel()
    handle.stop = stop
    try:
        if handle.cancelled.is_set():
            task.cancel()
            raise AccessError("CANCELLED", "执行已取消", 409)
        result = await task
        # Do not expose env values, pod specs, annotations, or managedFields in
        # diagnostic results. They can contain application credentials.
        def summary(item):
            metadata = item.get("metadata", {})
            return {"kind": item.get("kind", body["kind"]), "name": metadata.get("name"), "namespace": metadata.get("namespace"),
                    "uid": metadata.get("uid"), "generation": metadata.get("generation"), "status": item.get("status", {}),
                    "created_at": metadata.get("creationTimestamp"),
                    "owners": [{key: owner.get(key) for key in ("kind", "name", "uid")} for owner in metadata.get("ownerReferences", [])],
                    **({"desired_replicas": item.get("spec", {}).get("replicas", 1)} if body["kind"] == "deployments" else {}),
                    **({"reason": item.get("reason"), "message": item.get("message"), "type": item.get("type"), "involvedObject": item.get("involvedObject")} if body["kind"] == "events" else {})}
        if isinstance(result, str):
            return {"namespace": namespace, "name": body["name"], "logs": result, "tail_lines": body["tail_lines"], "bounded": True}
        response = {"namespace": namespace, "operation": body["operation"],
                    **({"items": [summary(i) for i in result["items"]], "truncated": bool(result.get("metadata", {}).get("continue"))} if "items" in result else {"resource": summary(result)})}
        if body["operation"] in {"restart", "rollout"}:
            response["verification"] = {
                "target_generation": result.get("metadata", {}).get("generation"),
                "rollout_observed": body["operation"] == "rollout",
                "pods_observed": False,
                "maintenance_verified": False,
                "remaining_checks": (["After this execution succeeds, submit a fresh k8s.read operation=rollout for this deployment and await its terminal result."] if body["operation"] == "restart" else [])
                    + ["Read current ReplicaSets and Pods for this deployment; verify the new ReplicaSet's Pods are Ready. Old ready replicas are not recovery evidence."],
            }
        return response
    except asyncio.CancelledError:
        raise AccessError("CANCELLATION_UNCONFIRMED", "Kubernetes 请求已中止，远端状态需核实", 409)
    finally:
        handle.stop = None


async def inspect_kubernetes(resource, account, secret):
    await kube_request(resource, secret, "GET", "/version")
    # SSRR includes effective RBAC. Unknown/incomplete permissions fail closed.
    safe = True
    for namespace in resource["namespaces"]:
        review = await kube_request(resource, secret, "POST", "/apis/authorization.k8s.io/v1/selfsubjectrulesreviews",
            body={"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectRulesReview", "spec": {"namespace": namespace}})
        status = review.get("status", {})
        if status.get("incomplete") or status.get("evaluationError") or "resourceRules" not in status:
            safe = False
        for rule in status.get("resourceRules", []):
            if set(rule.get("verbs", [])) - {"get", "list", "watch"}:
                # Kubernetes grants self-introspection to authenticated identities.
                if not (set(rule.get("apiGroups", [])) <= {"authorization.k8s.io", "authentication.k8s.io"}
                        and set(rule.get("resources", [])) <= {"selfsubjectaccessreviews", "selfsubjectrulesreviews", "selfsubjectreviews"}):
                    safe = False
    return {"connected": True, "normal_safe": safe, "actual_user": account["username"], "permission_summary": {"namespaces": resource["namespaces"], "read_only_rbac": safe}}
