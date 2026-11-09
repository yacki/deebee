import { API_BASE, authToken } from './api';

export type AIAccess = { resourceId: string; csrf: string };
export type AIStatus = { enabled: boolean; share_schema: boolean };
export type AISettings = AIStatus & { base_url: string; model: string; has_api_key: boolean; timeout_seconds: number };

export async function aiRequest<T>(path: string, options: { body?: unknown; method?: string; signal?: AbortSignal; access?: AIAccess } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.access) headers['X-CSRF-Token'] = options.access.csrf;
  else if (authToken()) headers.Authorization = `Bearer ${authToken()}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method || (options.body === undefined ? 'GET' : 'POST'), headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    credentials: 'same-origin', signal: options.signal ?? AbortSignal.timeout(35000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || 'AI 服务暂时不可用');
  return result as T;
}
