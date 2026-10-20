<script setup lang="ts">
import { computed, ref } from 'vue';
import { expiryTimestamp, localDateTime } from '../accessPolicy';

// Management entities are validated by the backend.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const props = defineProps<{ records: Record<string, Row[]>; roleId?: string; principalId?: string; request: (path: string, method?: string, body?: unknown) => Promise<Row>; reload: () => Promise<void> }>();
const selected = ref<string[]>([]), starts = ref(''), expires = ref(''), busy = ref(false), error = ref(''), message = ref('');
const targetKind = computed(() => props.roleId ? 'principals' : 'roles');
const choices = computed(() => props.records[targetKind.value] || []);
const members = computed(() => (props.records['role-memberships'] || []).filter(m => props.roleId ? m.role_id === props.roleId : m.principal_id === props.principalId));
function name(m: Row) { return choices.value.find(c => c.id === (props.roleId ? m.principal_id : m.role_id))?.name || '已删除'; }
function state(m: Row) {
  const now = Date.now() / 1000;
  const target = choices.value.find(c => c.id === (props.roleId ? m.principal_id : m.role_id));
  if (!m.enabled) return '已移除';
  if (!target?.enabled) return '对象已停用';
  if (m.expires_at && m.expires_at <= now) return '已到期';
  if (m.starts_at && m.starts_at > now) return '未生效';
  return '生效中';
}
function date(value: number) { return value ? new Date(value * 1000).toLocaleString() : '不限'; }
function edit(m: Row) { selected.value = [props.roleId ? m.principal_id : m.role_id]; starts.value = localDateTime(m.starts_at); expires.value = localDateTime(m.expires_at); }
async function save(enabled: boolean, ids = selected.value) {
  busy.value = true; error.value = ''; message.value = '';
  try {
    const changes = ids.map(id => {
      const old = members.value.find(m => (props.roleId ? m.principal_id : m.role_id) === id);
      return { role_id: props.roleId || id, principal_id: props.principalId || id, enabled,
        starts_at: enabled ? expiryTimestamp(starts.value) : old?.starts_at || null,
        expires_at: enabled ? expiryTimestamp(expires.value) : old?.expires_at || null,
        expected_version: old?.version ?? null };
    });
    await props.request('/admin/v1/role-memberships/batch', 'POST', { changes });
    await props.reload(); selected.value = []; starts.value = ''; expires.value = '';
    message.value = enabled ? '用户组成员已保存。' : '已移除用户组关系，其他有效授权仍可使用。';
  } catch (e) { error.value = e instanceof Error ? e.message : '保存失败'; }
  finally { busy.value = false; }
}
</script>

<template>
  <section aria-label="用户组成员配置">
    <h3>{{ roleId ? '用户组成员' : '所属用户组' }}</h3>
    <a-alert v-if="error" type="error">{{ error }}</a-alert><a-alert v-if="message" type="success">{{ message }}</a-alert>
    <div class="access-fields">
      <div class="access-field wide-field">{{ roleId ? '选择系统账号' : '选择用户组' }}<a-select v-model="selected" role="combobox" multiple allow-search :aria-label="roleId ? '选择系统账号' : '选择用户组'" :options="choices.map(c=>({value:c.id,label:c.name+(c.enabled?'':'（已停用）')}))" /></div>
      <div class="access-field">成员开始时间<input v-model="starts" type="datetime-local" aria-label="成员开始时间" /></div>
      <div class="access-field">成员到期时间<input v-model="expires" type="datetime-local" aria-label="成员到期时间" /></div>
    </div>
    <div class="access-actions"><a-button type="primary" :disabled="busy||!selected.length" @click="save(true)">{{ roleId ? '添加成员 / 保存有效期' : '分配用户组 / 保存有效期' }}</a-button><a-button :disabled="busy||!selected.length" @click="save(false)">移除所选</a-button></div>
    <a-table :data="members" row-key="id" :pagination="{pageSize:10}" :loading="busy">
      <template #columns>
        <a-table-column :title="roleId?'系统账号':'用户组'"><template #cell="{record:m}">{{ name(m) }}</template></a-table-column>
        <a-table-column title="有效期"><template #cell="{record:m}">{{ date(m.starts_at) }} → {{ date(m.expires_at) }}</template></a-table-column>
        <a-table-column title="状态"><template #cell="{record:m}">{{ state(m) }}</template></a-table-column>
        <a-table-column title="操作"><template #cell="{record:m}"><a-button @click="edit(m)">编辑有效期</a-button><a-button v-if="m.enabled" :disabled="busy" @click="save(false,[roleId?m.principal_id:m.role_id])">移除</a-button></template></a-table-column>
      </template>
    </a-table>
  </section>
</template>

<style scoped>
section{margin:22px 0}h3{font-size:14px;font-weight:500;margin:0 0 16px}.access-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.access-field{display:flex;flex-direction:column;gap:8px}.wide-field{grid-column:1/-1}.access-actions{display:flex;gap:8px;margin:16px 0;flex-wrap:wrap}input[type=datetime-local]{font:inherit;border:1px solid var(--color-border-2);padding:8px;background:var(--color-fill-2);min-width:0}.arco-alert{margin-bottom:16px}
</style>
