<script setup lang="ts">
import { computed, ref } from 'vue';
import RoleMemberships from './RoleMemberships.vue';
import { optionLabel } from '../accessPolicy';

// Management entities are validated by the backend.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const props = defineProps<{ records: Record<string, Row[]>; request: (path: string, method?: string, body?: unknown) => Promise<Row>; reload: () => Promise<void> }>();
const emit = defineEmits<{ edit: [role: Row]; grant: [role: Row]; editGrant: [grant: Row] }>();
const selected = ref(''), search = ref(''), busy = ref(false), error = ref(''), modal = ref(''), confirmName = ref('');
const activities = ref<Row[]>([]);
const nextActivity = ref<number | null>(null);
const roles = computed(() => (props.records.roles || []).filter(r => r.name.toLowerCase().includes(search.value.toLowerCase())));
const role = computed(() => props.records.roles?.find(r => r.id === selected.value));
const grants = computed(() => (props.records['access-grants'] || []).filter(g => g.role_id === selected.value));
function resource(id: string) { return props.records.resources?.find(r => r.id === id)?.name || id; }
function accounts(g: Row) { return [g.normal_account_id, g.allow_privileged && g.privileged_account_id].filter(Boolean).map(id => props.records.accounts?.find(a => a.id === id)?.username || id).join('、'); }
async function run(action: () => Promise<void>) { busy.value = true; error.value = ''; try { await action(); } catch (e) { error.value = e instanceof Error ? e.message : '操作失败'; } finally { busy.value = false; } }
async function toggle(r: Row) { await run(async () => { await props.request('/admin/v1/roles/' + r.id, 'PATCH', { expected_version: r.version, enabled: !r.enabled }); await props.reload(); }); }
async function activity(more = false) { await run(async () => { const result = await props.request('/admin/v1/roles/' + selected.value + '/activities' + (more && nextActivity.value ? '?before=' + nextActivity.value : '')); activities.value = more ? [...activities.value, ...result.items] : result.items; nextActivity.value = result.next_before; }); }
async function submitModal() { await run(async () => {
  if (!role.value) return;
  const r = role.value;
  if (modal.value === 'clone') await props.request('/admin/v1/roles/' + r.id + '/clone', 'POST', { expected_version: r.version, name: confirmName.value });
  else { await props.request('/admin/v1/roles/' + r.id, 'DELETE', { expected_version: r.version, confirm_name: confirmName.value }); selected.value = ''; }
  modal.value = ''; await props.reload();
}); }
</script>

<template>
  <a-alert v-if="error" type="error">{{ error }}</a-alert>
  <section v-if="role" class="access-panel role-detail">
    <a-button @click="selected=''">返回用户组列表</a-button>
    <div class="access-heading"><div><h2>{{ role.name }}</h2><p>{{ role.description || '资源访问用户组' }} · {{ role.enabled?'已启用':'已停用' }}</p></div><a-button @click="emit('edit',role)">编辑用户组</a-button><a-button :disabled="busy" @click="toggle(role)">{{ role.enabled?'停用用户组':'启用用户组' }}</a-button><a-button @click="modal='clone';confirmName=role.name+' 副本'">复制用户组</a-button><a-button status="danger" @click="modal='delete';confirmName=''">删除用户组</a-button></div>
    <RoleMemberships :records="records" :role-id="role.id" :request="request" :reload="reload" />
    <h3>资源授权</h3><p>每条授权单独配置资源、账号和操作，成员继承这些授权。复制用户组会复制停用的授权，不复制成员。</p>
    <a-button type="primary" @click="emit('grant',role)">添加资源授权</a-button>
    <a-table :data="grants" row-key="id" :pagination="{pageSize:10}">
      <template #columns>
        <a-table-column title="资源"><template #cell="{record:g}">{{ resource(g.resource_id) }}</template></a-table-column>
        <a-table-column title="资源账号"><template #cell="{record:g}">{{ accounts(g) }}</template></a-table-column>
        <a-table-column title="允许操作"><template #cell="{record:g}">{{ g.actions.map(optionLabel).join('、') }}</template></a-table-column>
        <a-table-column title="状态"><template #cell="{record:g}">{{ !g.enabled?'已停用':g.expires_at&&g.expires_at*1000<=Date.now()?'已到期':g.starts_at&&g.starts_at*1000>Date.now()?'未生效':'已授权' }}</template></a-table-column>
        <a-table-column title="操作"><template #cell="{record:g}"><a-button @click="emit('editGrant',g)">修改授权</a-button></template></a-table-column>
      </template>
    </a-table>
    <h3>变更记录</h3><a-button :disabled="busy" @click="activity()">查看用户组活动</a-button><p v-for="a in activities" :key="a.id">{{ new Date(a.at*1000).toLocaleString() }} · {{ a.actor_name||a.actor }} · {{ a.operation||a.action }}</p><a-button v-if="nextActivity" :disabled="busy" @click="activity(true)">更早的活动</a-button>
  </section>
  <section v-else class="role-list">
    <div class="access-field">搜索用户组<a-input v-model="search" :input-attrs="{'aria-label':'搜索用户组'}" /></div>
    <a-table :data="roles" row-key="id" :pagination="{pageSize:20}" :loading="busy">
      <template #columns>
        <a-table-column title="用户组"><template #cell="{record:r}"><a-button type="text" @click="selected=r.id;activities=[]">{{ r.name }}</a-button><small>{{ r.description }}</small></template></a-table-column>
        <a-table-column title="成员 / 资源"><template #cell="{record:r}">{{ (records['role-memberships']||[]).filter(m=>m.role_id===r.id&&m.enabled).length }} 位成员 · {{ (records['access-grants']||[]).filter(g=>g.role_id===r.id&&g.enabled).length }} 条授权</template></a-table-column>
        <a-table-column title="状态"><template #cell="{record:r}">{{ r.enabled?'已启用':'已停用' }}</template></a-table-column>
        <a-table-column title="操作"><template #cell="{record:r}"><a-button @click="selected=r.id;activities=[]">配置成员与资源</a-button><a-button @click="emit('edit',r)">编辑</a-button><a-button :disabled="busy" @click="toggle(r)">{{ r.enabled?'停用':'启用' }}</a-button></template></a-table-column>
      </template>
    </a-table>
  </section>
  <a-modal :visible="!!modal" :title="modal==='clone'?'复制用户组':'删除用户组'" :ok-loading="busy" :ok-button-props="{disabled:modal==='delete'?confirmName!==role?.name:!confirmName.trim()}" @ok="submitModal" @cancel="modal=''">
    <p>{{ modal==='clone'?'填写新用户组名称。新用户组及复制的资源授权均停用，不复制成员。':'删除后撤销该用户组的成员和资源授权，历史审计保留。请输入完整用户组名称：'+role?.name }}</p><a-input v-model="confirmName" :input-attrs="{'aria-label':modal==='clone'?'新用户组名称':'确认删除的用户组名称'}" /><a-alert v-if="error" type="error">{{ error }}</a-alert>
  </a-modal>
</template>

<style scoped>
.role-detail{padding:22px;background:var(--color-bg-1);border:1px solid var(--color-border-2);border-radius:4px}.access-heading{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap;margin:20px 0}.access-heading>div{flex:1;min-width:200px}h2{font-size:16px;font-weight:500;margin:0 0 12px}h3{font-size:14px;font-weight:500;margin:24px 0 12px}p{line-height:1.7;color:var(--color-text-3)}small{display:block;color:var(--color-text-3);font-size:12px}.access-field{display:flex;flex-direction:column;gap:8px;margin-bottom:16px}.role-detail :deep(.arco-table){margin-top:14px}.role-detail :deep(td){overflow-wrap:anywhere}.role-list{margin-top:16px}
</style>
