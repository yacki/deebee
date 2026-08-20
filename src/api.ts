export const API_BASE = import.meta.env.VITE_DEEBEE_API_URL || "http://127.0.0.1:8000/api";

export function authToken() { return sessionStorage.getItem("deebee_token") || ""; }

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = authToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string }; detail?: string } | null;
    throw new Error(payload?.error?.message || payload?.detail || `请求失败 (${response.status})`);
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
