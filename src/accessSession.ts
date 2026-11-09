import { API_BASE } from './api';

export type AccessSession = { resourceId: string; mode: string; optionId: string; csrf: string };

/** A session id also requires the same authenticated browser cookie and Origin. */
export async function createAccessSocket(access: AccessSession): Promise<string> {
  const response = await fetch(`${API_BASE}/v1/sessions`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': access.csrf },
    body: JSON.stringify({ resource_id: access.resourceId, mode: access.mode, access_option_id: access.optionId }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || '会话授权失败，请刷新可用资源。');
  const url = new URL(`${API_BASE}/v1/sessions/${encodeURIComponent(data.id)}/${data.protocol}`, window.location.href);
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}
