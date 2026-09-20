<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Icon } from '@iconify/vue';
// Server records include adapter-specific connection details.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const props = defineProps<{ resources: Row[]; accounts: Row[]; busy: boolean }>();
const emit = defineEmits<{ edit: [row: Row]; accounts: [row: Row]; toggle: [row: Row]; remove: [row: Row] }>();
const search = ref(''), environment = ref(''), project = ref(''), tag = ref(''), type = ref(''), state = ref(''), page = ref(1);
const view = ref('cards');
const values = (key: string) => [...new Set(props.resources.flatMap(r => Array.isArray(r[key]) ? r[key] : r[key] ? [r[key]] : []))].sort();
const filtered = computed(() => props.resources.filter(r =>
  (!search.value || [r.name,r.host,r.database,...(r.tags||[])].join(' ').toLowerCase().includes(search.value.toLowerCase())) &&
  (!environment.value || r.environment === environment.value) && (!project.value || r.project_groups?.includes(project.value)) &&
  (!tag.value || r.tags?.includes(tag.value)) && (!type.value || r.type === type.value) && (!state.value || String(r.enabled) === state.value)));
const shown = computed(() => filtered.value.slice((page.value-1)*12,page.value*12));
watch([search,environment,project,tag,type,state,()=>props.resources],()=>{page.value=1;});
function reset(){search.value='';environment.value='';project.value='';tag.value='';type.value='';state.value='';}
const accountsFor = (id:string) => props.accounts.filter(a=>a.resource_id===id);
function sourceNotice(r:Row){return String(r.connection_sync?.reason||'').replaceAll('已入池；','').replaceAll('已加入资源管理；','').replaceAll('前台连接','工作台连接').replaceAll('启用 MCP 资源','启用资源') || (r.connection_sync?'工作台连接 · 自动同步':'手动登记');}
</script>
<template>
  <div class="catalog">
    <div class="catalog-stats"><a-card><span>资源总数</span><b>{{ resources.length }}</b></a-card><a-card><span>已启用</span><b>{{ resources.filter(r=>r.enabled).length }}</b></a-card><a-card><span>环境</span><b>{{ values('environment').length }}</b></a-card><a-card><span>项目组</span><b>{{ values('project_groups').length }}</b></a-card></div>
    <a-card class="catalog-filter">
      <a-input-search v-model="search" aria-label="搜索资源" placeholder="资源名称 / 地址 / 标签" allow-clear />
      <a-select v-model="environment" aria-label="环境筛选" placeholder="全部环境" allow-clear :options="values('environment')" />
      <a-select v-model="project" aria-label="项目组筛选" placeholder="全部项目组" allow-clear :options="values('project_groups')" />
      <a-select v-model="tag" aria-label="标签筛选" placeholder="全部标签" allow-clear :options="values('tags')" />
      <a-select v-model="type" aria-label="资源类型筛选" placeholder="全部类型" allow-clear :options="values('type')" />
      <a-select v-model="state" aria-label="资源状态筛选" placeholder="全部状态" allow-clear :options="[{value:'true',label:'已启用'},{value:'false',label:'未启用'}]" />
      <a-button @click="reset">重置筛选</a-button>
    </a-card>
    <div class="catalog-bar"><span>资源清单 <small>共 {{ filtered.length }} 项</small></span><a-radio-group v-model="view" type="button" size="small"><a-radio value="cards">卡片</a-radio><a-radio value="table">列表</a-radio></a-radio-group></div>
    <div v-if="view==='cards'" class="resource-grid">
      <a-card v-for="r in shown" :key="r.id" class="resource-card">
        <div class="resource-title"><Icon :icon="r.type==='ssh'?'lucide:server':'lucide:database'" :width="20" /><span>{{ r.name }}</span><a-tag :color="r.enabled?'green':'gray'">{{ r.enabled?'已启用':'未启用' }}</a-tag></div>
        <p>{{ r.type }} · {{ r.host }}:{{ r.port }}{{ r.database?' / '+r.database:'' }}</p>
        <div class="resource-meta"><span>环境</span><a-tag color="arcoblue">{{ r.environment||'未分类' }}</a-tag><span>项目组</span><div><a-tag v-for="p in r.project_groups" :key="p">{{ p }}</a-tag><small v-if="!r.project_groups?.length">未分组</small></div><span>标签</span><div><a-tag v-for="t in r.tags" :key="t" color="cyan">{{ t }}</a-tag><small v-if="!r.tags?.length">暂无标签</small></div></div>
        <p class="source-note">{{ sourceNotice(r) }}</p>
        <div class="account-heading">资源账号 <small>{{ accountsFor(r.id).length }} 个</small></div>
        <ul class="account-list"><li v-for="a in accountsFor(r.id)" :key="a.id"><span>{{ a.username }}</span><small>{{ !a.permission_confirmed?'权限待核实':a.tier==='privileged'?'高权限账号':'普通账号' }}</small><small>{{ a.enabled?'已启用':'未启用' }}</small></li></ul>
        <a-empty v-if="!accountsFor(r.id).length" description="暂无资源账号" />
        <div class="card-actions"><a-button type="primary" size="small" @click="emit('accounts',r)">资源账号</a-button><a-button size="small" @click="emit('edit',r)">编辑</a-button><a-button size="small" :disabled="busy||(!r.enabled&&r.connection_sync&&r.connection_sync.state!=='synced')" @click="emit('toggle',r)">{{ r.enabled?'停用':'启用' }}</a-button><a-button status="danger" size="small" @click="emit('remove',r)">删除</a-button></div>
      </a-card>
    </div>
    <a-table v-else :data="shown" :pagination="false" :scroll="{x:1050}" row-key="id">
      <template #columns>
        <a-table-column title="资源 / 地址"><template #cell="{record:r}"><a-link @click="emit('accounts',r)">{{ r.name }}</a-link><p>{{ r.type }} · {{ r.host }}:{{ r.port }}</p></template></a-table-column>
        <a-table-column title="环境" data-index="environment" /><a-table-column title="项目组"><template #cell="{record:r}">{{ r.project_groups?.join('、')||'—' }}</template></a-table-column><a-table-column title="标签"><template #cell="{record:r}">{{ r.tags?.join('、')||'—' }}</template></a-table-column>
        <a-table-column title="资源账号"><template #cell="{record:r}"><div v-for="a in accountsFor(r.id)" :key="a.id">{{ a.username }} <small>{{ a.enabled?'已启用':'未启用' }}</small></div></template></a-table-column>
        <a-table-column title="状态"><template #cell="{record:r}">{{ r.enabled?'已启用':'未启用' }}</template></a-table-column>
        <a-table-column title="操作" :width="220" fixed="right"><template #cell="{record:r}"><a-space size="mini"><a-button size="mini" @click="emit('accounts',r)">账号</a-button><a-button size="mini" @click="emit('edit',r)">编辑</a-button><a-button size="mini" status="danger" @click="emit('remove',r)">删除</a-button></a-space></template></a-table-column>
      </template>
    </a-table>
    <a-empty v-if="!filtered.length&&view==='cards'" description="没有符合条件的资源" />
    <a-pagination v-model:current="page" :total="filtered.length" :page-size="12" show-total />
  </div>
</template>
<style scoped>
.catalog-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:16px}.catalog-stats span{font-size:12px;color:var(--color-text-3)}.catalog-stats b{display:block;font-weight:400;font-size:24px;margin-top:8px}.catalog-filter :deep(.arco-card-body){display:grid;grid-template-columns:2fr repeat(3,1fr);gap:12px}.catalog-bar{display:flex;align-items:center;justify-content:space-between;margin:20px 0 12px}.catalog small{font-size:12px;color:var(--color-text-3)}.resource-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(350px,1fr));gap:16px}.resource-card{min-width:0}.resource-card :deep(.arco-card-body){display:flex;flex-direction:column;height:100%;padding:18px}.resource-title{display:flex;gap:10px;align-items:flex-start}.resource-title>span{flex:1;overflow-wrap:anywhere}.resource-title .arco-tag{flex:none}.resource-card p{font-size:12px;color:var(--color-text-3);overflow-wrap:anywhere;line-height:1.7;margin:10px 0}.resource-meta{display:grid;grid-template-columns:48px minmax(0,1fr);gap:10px;align-items:start;font-size:12px;padding:12px 0;border-top:1px solid var(--color-border-1)}.resource-meta>span{color:var(--color-text-3)}.resource-meta .arco-tag{margin:0 5px 4px 0;width:fit-content}.source-note{min-height:40px}.account-heading{border-top:1px solid var(--color-border-1);padding-top:12px}.account-list{list-style:none;padding:0;margin:6px 0 18px}.account-list li{display:flex;gap:12px;padding:8px 0;align-items:center}.account-list li>span{flex:1;overflow-wrap:anywhere}.card-actions{display:grid;grid-template-columns:1.4fr repeat(3,1fr);gap:8px;margin-top:auto;padding-top:16px;border-top:1px solid var(--color-border-2)}.catalog>.arco-pagination{justify-content:flex-end;margin:20px 0}.catalog .arco-empty{padding:24px}.catalog :deep(.arco-table-th){font-weight:400}@media(max-width:900px){.catalog-filter :deep(.arco-card-body){grid-template-columns:1fr 1fr}.resource-grid{grid-template-columns:1fr}}@media(max-width:600px){.catalog-stats{grid-template-columns:1fr 1fr}}
</style>
