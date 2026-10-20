<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import JsonPanel from './JsonPanel.vue';
import { optionLabel } from '../accessPolicy';

// Server-validated management records follow the existing administration API.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const props = defineProps<{ request: (path: string, method?: string, body?: unknown) => Promise<Row>; resources: Row[]; principals: Row[] }>();
const tab = ref('requests'), status = ref('pending_approval'), resourceFilter = ref(''), ownerFilter = ref('');
const rows = ref<Row[]>([]), rules = ref<Row[]>([]), templates = ref<Row[]>([]), total = ref(0), page = ref(1);
const busy = ref(false), error = ref(''), message = ref(''), detail = ref<Row | null>(null), comment = ref('');
const form = ref<Row | null>(null), editing = ref<Row | null>(null), selectedTemplate = ref('');
const eligibleResources = computed(() => props.resources.filter(r => form.value?.protocol === 'ssh' ? r.type === 'ssh' : ['mysql', 'postgresql'].includes(r.type)));
const builtinTemplates = computed(() => templates.value.filter(t => t.protocol === form.value?.protocol));
function resource(id: string) { return props.resources.find(r => r.id === id); }
function person(id: string) { const p = props.principals.find(p => p.id === id); return p?.name || id; }
function date(value?: number) { return value ? new Date(value * 1000).toLocaleString('zh-CN', {hour12: false}) : '—'; }
async function act(fn: () => Promise<void>) { busy.value = true; error.value = ''; message.value = ''; try { await fn(); } catch (e) { error.value = e instanceof Error ? e.message : '操作失败'; } finally { busy.value = false; } }
async function loadRequests() {
  const q = new URLSearchParams({status: status.value, resource_id: resourceFilter.value, owner: ownerFilter.value, offset: String((page.value - 1) * 20), limit: '20'});
  const result = await props.request('/admin/v1/approvals?' + q);
  rows.value = result.items; total.value = result.total;
}
async function refresh() { await act(async () => {
  const [savedRules, savedTemplates] = await Promise.all([props.request('/admin/v1/approval-rules'), props.request('/admin/v1/approval-templates')]);
  rules.value = savedRules.items; templates.value = savedTemplates.items; await loadRequests();
}); }
async function filter() { page.value = 1; await act(loadRequests); }
async function inspect(row: Row) { await act(async () => { detail.value = await props.request('/admin/v1/executions/' + row.id); comment.value = ''; }); }
async function reloadDetail() { await act(async () => { if (detail.value) detail.value = await props.request('/admin/v1/executions/' + detail.value.id); await loadRequests(); }); }
async function decide(decision: 'approve' | 'reject') { await act(async () => {
  if (!detail.value || !comment.value.trim()) throw new Error('请填写审核意见。');
  const id = detail.value.id;
  const result = await props.request('/admin/v1/approvals/' + id + '/decision', 'POST', {decision, comment: comment.value.trim(), request_hash: detail.value.request_hash});
  detail.value = await props.request('/admin/v1/executions/' + id); await loadRequests();
  message.value = result.status === 'expired' ? result.error?.message : decision === 'approve' ? '已通过，原申请将按原有授权执行。可刷新查看执行结果。' : '已拒绝，原命令未执行。';
}); }
function start(rule?: Row) {
  editing.value = rule || null;
  const fields = ['name', 'description', 'protocol', 'match_type', 'pattern', 'enabled', 'resource_ids', 'expires_minutes'];
  form.value = rule ? Object.fromEntries(fields.map(k => [k, Array.isArray(rule[k]) ? [...rule[k]] : rule[k]])) : {name: '', description: '', protocol: 'ssh', match_type: 'contains', pattern: '', enabled: true, resource_ids: [], expires_minutes: 30};
  error.value = ''; message.value = '';
}
function fromTemplate() {
  const template = templates.value.find(t => t.template_id === selectedTemplate.value);
  if (!template) return;
  start(template); editing.value = null;
}
function changeProtocol() { if (form.value) { form.value.resource_ids = []; if (form.value.match_type === 'builtin') form.value.pattern = builtinTemplates.value[0]?.pattern || ''; } }
async function save() { await act(async () => {
  if (!form.value) return;
  await props.request('/admin/v1/approval-rules' + (editing.value ? '/' + editing.value.id : ''), editing.value ? 'PATCH' : 'POST', {...form.value, ...(editing.value ? {expected_version: editing.value.version} : {})});
  form.value = null; rules.value = (await props.request('/admin/v1/approval-rules')).items;
  message.value = '规则已保存。规则变化后，旧的待审批申请需重新提交。';
}); }
async function toggle(rule: Row) { await act(async () => { await props.request('/admin/v1/approval-rules/' + rule.id, 'PATCH', {expected_version: rule.version, enabled: !rule.enabled}); rules.value = (await props.request('/admin/v1/approval-rules')).items; }); }
async function remove(rule: Row) { await act(async () => { await props.request('/admin/v1/approval-rules/' + rule.id, 'DELETE', {expected_version: rule.version}); rules.value = (await props.request('/admin/v1/approval-rules')).items; message.value = '规则已删除，可从模板重新添加。'; }); }
onMounted(refresh);
defineExpose({ refresh });
</script>

<template>
  <div class="approval-center">
    <a-alert v-if="error" type="error" role="alert">{{ error }}</a-alert>
    <a-alert v-if="message" type="success" role="status">{{ message }}</a-alert>
    <div class="approval-tabs">
      <a-button :type="tab==='requests'?'primary':'secondary'" @click="tab='requests'">审批申请</a-button>
      <a-button :type="tab==='rules'?'primary':'secondary'" @click="tab='rules'">审批规则与模板</a-button>
    </div>
    <template v-if="tab==='requests'">
      <div class="approval-filters">
        <div class="access-field">申请状态<a-select v-model="status" aria-label="申请状态" role="combobox" @change="filter"><a-option value="">全部状态</a-option><a-option v-for="s in ['pending_approval','queued','running','succeeded','failed','rejected','expired','cancelled','unknown']" :key="s" :value="s">{{ optionLabel(s) }}</a-option></a-select></div>
        <div class="access-field">资源<a-select v-model="resourceFilter" aria-label="审批资源筛选" role="combobox" allow-clear @change="filter"><a-option v-for="r in resources.filter(r=>['ssh','mysql','postgresql'].includes(r.type))" :key="r.id" :value="r.id">{{ r.name }}</a-option></a-select></div>
        <div class="access-field">申请人<a-select v-model="ownerFilter" aria-label="审批申请人筛选" role="combobox" allow-clear @change="filter"><a-option v-for="p in principals" :key="p.id" :value="p.id">{{ p.name }}</a-option></a-select></div>
        <a-button :disabled="busy" @click="act(loadRequests)">查询申请</a-button>
      </div>
      <a-table :data="rows" row-key="id" :loading="busy" :pagination="false" :scroll="{x:900}">
        <template #columns>
          <a-table-column title="申请人 / 操作"><template #cell="{record:r}">{{ person(r.owner) }}<small>{{ optionLabel(r.tool) }} · {{ date(r.created_at) }}</small></template></a-table-column>
          <a-table-column title="目标资源 / 账号"><template #cell="{record:r}">{{ r.target?.name || resource(r.resource_id)?.name || r.resource_id }}<small>{{ r.username }} · {{ optionLabel(r.mode) }}</small></template></a-table-column>
          <a-table-column title="触发规则"><template #cell="{record:r}">{{ r.approval.rules.map((rule:Row)=>rule.name).join('、') }}<small>{{ r.approval.reason || '未填写申请原因' }}</small></template></a-table-column>
          <a-table-column title="状态"><template #cell="{record:r}"><a-tag :color="r.status==='pending_approval'?'orange':r.status==='succeeded'?'green':'gray'">{{ optionLabel(r.status) }}</a-tag><small v-if="r.status==='pending_approval'">到期 {{ date(r.approval.expires_at) }}</small></template></a-table-column>
          <a-table-column title="操作"><template #cell="{record:r}"><a-button :disabled="busy" @click="inspect(r)">{{ r.status==='pending_approval'?'查看并审核':'查看详情' }}</a-button></template></a-table-column>
        </template>
      </a-table>
      <a-pagination v-model:current="page" :total="total" :page-size="20" show-total @change="act(loadRequests)" />
    </template>
    <template v-else>
      <a-alert type="info">内置模板已默认启用。规则对所有普通系统账号及 Agent 生效，审批通过仍需原有资源授权。SSH 启用任一规则后使用逐条命令执行；固定只读检查仍可直接使用。</a-alert>
      <div class="approval-toolbar">
        <a-button type="primary" :disabled="busy" @click="start()">新增审批规则</a-button>
        <a-select v-model="selectedTemplate" aria-label="危险命令模板" role="combobox" placeholder="选择危险命令模板"><a-option v-for="t in templates" :key="t.template_id" :value="t.template_id">{{ t.name }}</a-option></a-select>
        <a-button :disabled="busy||!selectedTemplate" @click="fromTemplate">从模板新增</a-button>
      </div>
      <form v-if="form" class="approval-form" @submit.prevent="save">
        <h2>{{ editing?'编辑审批规则':'新增审批规则' }}</h2>
        <div class="approval-fields">
          <div class="access-field">规则名称<a-input v-model="form.name" :input-attrs="{'aria-label':'审批规则名称'}" required /></div>
          <div class="access-field">适用协议<a-select v-model="form.protocol" aria-label="审批协议" role="combobox" @change="changeProtocol"><a-option value="ssh">SSH</a-option><a-option value="database">数据库（MySQL / PostgreSQL）</a-option></a-select></div>
          <div class="access-field">匹配方式<a-select v-model="form.match_type" aria-label="审批匹配方式" role="combobox"><a-option value="builtin">内置危险操作检测</a-option><a-option value="contains">包含指定文本</a-option><a-option value="prefix">以指定文本开头</a-option><a-option value="all">全部操作</a-option></a-select></div>
          <div v-if="form.match_type==='builtin'" class="access-field">内置检测<a-select v-model="form.pattern" aria-label="内置危险检测" role="combobox"><a-option v-for="t in builtinTemplates" :key="t.pattern" :value="t.pattern">{{ t.name }}</a-option></a-select></div>
          <div v-else-if="form.match_type!=='all'" class="access-field">匹配文本<a-input v-model="form.pattern" :input-attrs="{'aria-label':'审批匹配文本'}" required /><small>忽略大小写并合并空白；数据库同时检测去除注释的 SQL。文本规则用于补充，不能单独防止 Shell 绕过。</small></div>
          <div class="access-field">适用资源<a-select v-model="form.resource_ids" multiple allow-clear aria-label="审批适用资源" role="combobox" placeholder="留空表示此协议的全部资源"><a-option v-for="r in eligibleResources" :key="r.id" :value="r.id">{{ r.name }}</a-option></a-select></div>
          <div class="access-field">审批有效期（分钟）<a-input-number v-model="form.expires_minutes" :min="1" :max="1440" :input-attrs="{'aria-label':'审批有效期（分钟）'}" /><small>同时受申请人会话和资源授权到期时间限制。</small></div>
          <div class="access-field">规则说明<a-textarea v-model="form.description" :textarea-attrs="{'aria-label':'审批规则说明'}" :auto-size="{minRows:2,maxRows:5}" /></div>
          <a-checkbox v-model="form.enabled">启用审批规则</a-checkbox>
        </div>
        <div class="approval-toolbar"><a-button type="primary" html-type="submit" :disabled="busy">保存审批规则</a-button><a-button :disabled="busy" @click="form=null">取消编辑</a-button></div>
      </form>
      <a-table :data="rules" row-key="id" :loading="busy" :pagination="{pageSize:20}" :scroll="{x:900}">
        <template #columns>
          <a-table-column title="规则"><template #cell="{record:r}">{{ r.name }}<small>{{ r.description }}</small></template></a-table-column>
          <a-table-column title="协议 / 范围"><template #cell="{record:r}">{{ r.protocol==='ssh'?'SSH':'数据库' }}<small>{{ r.resource_ids.length?r.resource_ids.map((id:string)=>resource(id)?.name||id).join('、'):'此协议的全部资源' }}</small></template></a-table-column>
          <a-table-column title="检测方式"><template #cell="{record:r}">{{ ({builtin:'内置检测',contains:'包含文本',prefix:'文本前缀',all:'全部操作'} as Row)[r.match_type] }}<small>{{ templates.find(t=>t.pattern===r.pattern)?.name||r.pattern }}</small><small>{{ r.expires_minutes }} 分钟</small></template></a-table-column>
          <a-table-column title="状态"><template #cell="{record:r}"><a-tag :color="r.enabled?'green':'gray'">{{ r.enabled?'已启用':'已停用' }}</a-tag></template></a-table-column>
          <a-table-column title="操作"><template #cell="{record:r}"><div class="approval-toolbar"><a-button :disabled="busy" @click="start(r)">编辑规则</a-button><a-button :disabled="busy" @click="toggle(r)">{{ r.enabled?'停用规则':'启用规则' }}</a-button><a-button :disabled="busy" status="danger" @click="remove(r)">删除规则</a-button></div></template></a-table-column>
        </template>
      </a-table>
    </template>
    <a-modal :visible="!!detail" title="操作审批详情" :width="960" :footer="false" @cancel="detail=null">
      <div v-if="detail" class="approval-detail">
        <a-alert v-if="error" type="error" role="alert">{{ error }}</a-alert><a-alert v-if="message" type="success" role="status">{{ message }}</a-alert>
        <dl><dt>申请人</dt><dd>{{ person(detail.owner) }}</dd><dt>目标资源</dt><dd>{{ (detail.target||resource(detail.resource_id))?.name }} · {{ (detail.target||resource(detail.resource_id))?.host }}:{{ (detail.target||resource(detail.resource_id))?.port }} {{ (detail.target||resource(detail.resource_id))?.database }}</dd><dt>资源账号</dt><dd>{{ detail.username }} · {{ optionLabel(detail.mode) }}</dd><dt>状态 / 到期时间</dt><dd>{{ optionLabel(detail.status) }} · {{ date(detail.approval.expires_at) }}</dd><dt>触发规则</dt><dd>{{ detail.approval.rules.map((r:Row)=>r.name).join('、') }}</dd><dt>申请原因</dt><dd>{{ detail.approval.reason||'未填写' }}</dd></dl>
        <h3>{{ detail.tool==='ssh.exec'?'待执行命令':'待执行 SQL' }}</h3><pre class="approval-command">{{ detail.request?.command || detail.request?.sql || '请求内容已过期' }}</pre>
        <template v-if="detail.request?.parameters&&Object.keys(detail.request.parameters).length"><h3>SQL 参数</h3><JsonPanel :value="detail.request.parameters" /></template>
        <p v-if="detail.request?.elevation==='sudo'">执行时使用 sudo 提权。</p>
        <template v-if="detail.status==='pending_approval'">
          <a-alert type="warning">通过后执行上方完整原始请求。请核对目标、资源账号、命令及 SQL 参数。</a-alert>
          <div class="access-field">审核意见<a-textarea v-model="comment" :textarea-attrs="{'aria-label':'审核意见'}" :max-length="2000" :auto-size="{minRows:3,maxRows:6}" /></div>
          <div class="approval-toolbar"><a-button type="primary" :disabled="busy||!comment.trim()" @click="decide('approve')">通过并执行</a-button><a-button status="danger" :disabled="busy||!comment.trim()" @click="decide('reject')">拒绝申请</a-button></div>
        </template>
        <p v-if="detail.approval.reviewer">审核人：{{ detail.approval.reviewer }} · {{ date(detail.approval.decided_at) }}<br>审核意见：{{ detail.approval.comment }}</p>
        <a-alert v-if="detail.error" type="warning">{{ detail.error.message }}</a-alert>
        <a-button :disabled="busy" @click="reloadDetail">刷新审批与结果</a-button>
        <template v-if="detail.result"><h3>执行结果</h3><JsonPanel :value="detail.result" /></template>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.approval-center{display:grid;gap:16px;min-width:0}.approval-tabs,.approval-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.approval-toolbar{margin:12px 0}.approval-toolbar>.arco-select{width:300px;max-width:100%}.approval-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) auto;gap:12px;align-items:end}.approval-form{padding:22px;background:white;border:1px solid var(--color-border-2)}.approval-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.access-field{display:flex;flex-direction:column;gap:8px}.approval-center small{display:block;margin-top:6px;color:var(--color-text-3);font-size:12px;overflow-wrap:anywhere}.approval-detail{display:grid;gap:14px}.approval-detail dl{display:grid;grid-template-columns:140px minmax(0,1fr);gap:12px;line-height:1.6}.approval-detail dt{color:var(--color-text-3)}.approval-detail dd{margin:0;overflow-wrap:anywhere}.approval-detail h3{margin:4px 0;font-size:14px;font-weight:500}.approval-command{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;max-height:320px;overflow:auto;background:var(--color-fill-2);border:1px solid var(--color-border-2);padding:16px;font:13px/1.7 ui-monospace,SFMono-Regular,monospace}.approval-detail p{line-height:1.7}.approval-detail .access-field{margin-top:10px}@media(max-width:800px){.approval-fields,.approval-filters{grid-template-columns:1fr}.approval-detail dl{grid-template-columns:1fr;gap:6px}}
</style>
