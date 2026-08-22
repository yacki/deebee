export const API_BASE = import.meta.env.VITE_DEEBEE_API_URL || "/api";

export class ApiError extends Error {
  constructor(message: string, readonly status = 0, readonly kind: "network" | "http" = "http", readonly code?: number | string) { super(message); this.name = "ApiError"; }
}

export function isNetworkError(reason: unknown) { return reason instanceof ApiError && reason.kind === "network"; }
export function isSessionExpiredError(reason: unknown) { return reason instanceof ApiError && reason.message.includes("查询会话已失效"); }

export function authToken() { return sessionStorage.getItem("deebee_token") || ""; }

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = authToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const method = (init.method || "GET").toUpperCase();
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    if (method === "GET" || method === "HEAD") {
      await new Promise(resolve => window.setTimeout(resolve, 350));
      try { response = await fetch(`${API_BASE}${path}`, { ...init, headers }); }
      catch { throw new ApiError("无法连接后台服务", 0, "network"); }
    } else {
      throw new ApiError("无法连接后台服务", 0, "network");
    }
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string; code?: number | string }; detail?: string } | null;
    throw new ApiError(payload?.error?.message || payload?.detail || `请求失败 (${response.status})`, response.status, "http", payload?.error?.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function download(path: string, filename: string) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${authToken()}` } });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error?.message || "下载失败");
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  URL.revokeObjectURL(url);
}

export function workspaceId() {
  const key = "deebee_workspace_id";
  let value = sessionStorage.getItem(key);
  if (!value) { value = crypto.randomUUID(); sessionStorage.setItem(key, value); }
  return value;
}

export const jsonBody = (value: unknown) => JSON.stringify(value);
