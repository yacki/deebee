/** Display vocabulary and lossless adapters for the existing access API. */
export const accessNavigation = [
  { id: 'identity-bindings', name: '外部身份', icon: 'lucide:link' },
  { id: 'principals', name: '系统账号', icon: 'lucide:users' },
  { id: 'resources', name: '资源管理', icon: 'lucide:server' },
  { id: 'access-grants', name: '资源授权', icon: 'lucide:badge-check' },
  { id: 'executions', name: '执行记录', icon: 'lucide:activity' },
  { id: 'audit-events', name: '审计日志', icon: 'lucide:scroll-text' },
  { id: 'integration', name: '系统设置', icon: 'lucide:settings' },
];

export const actionOptions = [
  { value: 'resources:read', label: '查看资源' },
  { value: 'db:query', label: '查询数据库' },
  { value: 'db:write', label: '修改数据库', warning: true },
  { value: 'ssh:exec', label: '执行服务器命令', warning: true },
  { value: 'privilege:use', label: '使用高权限资源账号', warning: true },
];

export function optionLabel(value: string): string {
  return ({ normal: '普通账号', privileged: '高权限账号', human: '人员', service: '程序 / Agent',
    password: '密码', private_key: 'SSH 私钥', managed: '由 DeeBee 验证',
    external_http: '由外部服务验证', jwt: '验证令牌签名', introspection: '向登录服务核验令牌',
    api_key: 'API Key', oidc: '企业登录（OIDC）', succeeded: '成功', failed: '失败',
    queued: '排队中', running: '执行中', cancelled: '已取消', unknown: '结果待确认',
    'ssh.exec': '执行服务器命令', 'db.schema': '查看数据库结构', 'db.query': '查询数据库',
    'db.execute': '修改数据库',
  } as Record<string, string>)[value] || value;
}

type Account = { id: string; resource_id: string; tier: string; enabled: boolean };
type GrantAccounts = { normal_account_id?: string; privileged_account_id?: string; allow_privileged?: boolean };
export function selectedGrantAccounts(grant: GrantAccounts): string[] {
  return [grant.normal_account_id, grant.allow_privileged ? grant.privileged_account_id : ''].filter((id): id is string => Boolean(id));
}

export function grantAccountFields(ids: string[], accounts: Account[], resourceId: string, enabled: boolean) {
  if (!ids.length) throw new Error('请选择至少一个资源账号。');
  const result = { normal_account_id: '', privileged_account_id: '', allow_privileged: false };
  for (const id of new Set(ids)) {
    const account = accounts.find(item => item.id === id && item.resource_id === resourceId);
    if (!account) throw new Error('资源账号不属于当前资源，请重新选择。');
    if (enabled && !account.enabled) throw new Error('请先在资源管理中检查并启用选中的资源账号。');
    if (!['normal', 'privileged'].includes(account.tier)) throw new Error('无法识别资源账号的权限类别。');
    const field = account.tier === 'normal' ? 'normal_account_id' : 'privileged_account_id';
    if (result[field]) throw new Error('同一权限类别只能选择一个资源账号。');
    result[field] = id;
  }
  result.allow_privileged = Boolean(result.privileged_account_id);
  return result;
}

export function localDateTime(seconds: number | null | undefined): string {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function expiryTimestamp(value: string): number | null {
  if (!value) return null;
  const time = new Date(value).getTime() / 1000;
  if (!Number.isFinite(time)) throw new Error('请选择有效的到期时间。');
  return time;
}
