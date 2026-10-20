<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Icon } from '@iconify/vue';
import ResourceIcon from './ResourceIcon.vue';
import { environmentLabel, resourceProduct } from '../resourcePresentation';
// Server records include adapter-specific connection details.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const props = defineProps<{ resources: Row[]; busy: boolean }>();
const emit = defineEmits<{ edit: [row: Row]; detail: [row: Row]; toggle: [row: Row]; remove: [row: Row] }>();
// null selects all resources; the empty string selects resources without an environment.
const environment = ref<string | null>(null);
const search = ref(''), project = ref(''), tag = ref(''), type = ref(''), state = ref(''), page = ref(1);
const view = ref('cards');
const values = (key: string): string[] => [...new Set<string>(props.resources.flatMap(r => Array.isArray(r[key]) ? r[key] : r[key] ? [r[key]] : []))].sort();
const environments = computed(() => values('environment'));
const unassigned = computed(() => props.resources.filter(r => !r.environment).length);
const count = (value: string) => props.resources.filter(r => (r.environment || '') === value).length;
const filtered = computed(() => props.resources.filter(r =>
  (!search.value || [r.name,r.host,r.database,...(r.tags||[])].join(' ').toLowerCase().includes(search.value.toLowerCase())) &&
  (environment.value === null || (r.environment || '') === environment.value) && (!project.value || r.project_groups?.includes(project.value)) &&
  (!tag.value || r.tags?.includes(tag.value)) && (!type.value || r.type === type.value) && (!state.value || String(r.enabled) === state.value)));
const shown = computed(() => filtered.value.slice((page.value-1)*12,page.value*12));
watch([search,environment,project,tag,type,state,()=>props.resources],()=>{page.value=1;});
// A renamed or removed environment must not leave an invisible filter active.
watch(environments, names => { if (environment.value && !names.includes(environment.value)) environment.value = null; });
function reset(){search.value='';environment.value=null;project.value='';tag.value='';type.value='';state.value='';}
function sourceNotice(r:Row){return String(r.connection_sync?.reason||'').replaceAll('已入池；','').replaceAll('已加入资源管理；','').replaceAll('前台连接','工作台连接').replaceAll('启用 MCP 资源','启用资源') || (r.connection_sync?'工作台连接 · 自动同步':'手动登记');}
</script>

<template>
  <div class="catalog">
    <div class="catalog-summary">
      <div><span class="summary-icon"><Icon icon="lucide:layers" width="22" /></span><div><b>{{ resources.length }}</b><span>全部资源</span></div></div>
      <div><span class="summary-icon green"><Icon icon="lucide:circle-check" width="22" /></span><div><b>{{ resources.filter(r=>r.enabled).length }}</b><span>已启用</span></div></div>
      <div><span class="summary-icon violet"><Icon icon="lucide:folders" width="22" /></span><div><b>{{ environments.length }}</b><span>资源环境</span></div></div>
      <div><span class="summary-icon amber"><Icon icon="lucide:network" width="22" /></span><div><b>{{ values('type').length }}</b><span>资源类型</span></div></div>
    </div>

    <section class="environment-panel" aria-label="资源环境">
      <div class="environment-heading"><span>按环境浏览</span><small>在新增或编辑资源时选择环境，也可填写自定义名称</small></div>
      <div class="environment-tabs" role="group" aria-label="环境分组">
        <button :aria-pressed="environment===null" :class="{active:environment===null}" @click="environment=null"><Icon icon="lucide:layout-grid" width="16" />全部环境 <span>{{ resources.length }}</span></button>
        <button v-for="env in environments" :key="env" :aria-pressed="environment===env" :class="{active:environment===env}" @click="environment=env"><span class="environment-dot" :data-environment="env" />{{ environmentLabel(env) }}<small v-if="environmentLabel(env)!==env">{{ env }}</small><span>{{ count(env) }}</span></button>
        <button v-if="unassigned" :aria-pressed="environment===''" :class="{active:environment===''}" @click="environment=''">未分配环境 <span>{{ unassigned }}</span></button>
      </div>
    </section>

    <div class="catalog-filter">
      <a-input-search v-model="search" :input-attrs="{'aria-label':'搜索资源'}" placeholder="搜索名称、地址或标签" allow-clear />
      <a-select v-model="type" role="combobox" aria-label="资源类型筛选" placeholder="全部类型" allow-clear :options="values('type').map(t=>({value:t,label:resourceProduct(t).name}))" />
      <a-select v-model="project" role="combobox" aria-label="项目组筛选" placeholder="全部项目组" allow-clear :options="values('project_groups')" />
      <a-select v-model="tag" role="combobox" aria-label="标签筛选" placeholder="全部标签" allow-clear :options="values('tags')" />
      <a-select v-model="state" role="combobox" aria-label="资源状态筛选" placeholder="全部状态" allow-clear :options="[{value:'true',label:'已启用'},{value:'false',label:'未启用'}]" />
      <a-button type="text" @click="reset">重置</a-button>
    </div>
    <div class="catalog-bar"><div><b>{{ environment===null?'全部资源':environmentLabel(environment) }}</b><small>{{ filtered.length }} 项资源</small></div><a-radio-group v-model="view" type="button" size="small" aria-label="资源展示方式"><a-radio value="cards"><Icon icon="lucide:layout-grid" width="14" /> 卡片</a-radio><a-radio value="table"><Icon icon="lucide:list" width="14" /> 列表</a-radio></a-radio-group></div>
    <div v-if="view==='cards'" class="resource-grid">
      <article v-for="r in shown" :key="r.id" class="resource-card">
        <div class="resource-title"><span class="product-tile"><ResourceIcon :type="r.type" :size="34" /></span><div><button class="resource-name" @click="emit('detail',r)">{{ r.name }}</button><small>{{ resourceProduct(r.type).name }}</small></div><span class="resource-status" :class="{enabled:r.enabled}"><i />{{ r.enabled?'已启用':'未启用' }}</span></div>
        <div class="resource-address"><Icon icon="lucide:network" width="14" /><span>{{ r.host }}:{{ r.port }}</span></div>
        <div v-if="r.database" class="resource-database"><Icon icon="lucide:database" width="14" /><span>{{ r.database }}</span></div>
        <div class="resource-meta"><span class="environment-badge"><Icon icon="lucide:folder" width="13" />{{ environmentLabel(r.environment) }}</span><a-tag v-for="p in r.project_groups" :key="p" size="small">{{ p }}</a-tag><a-tag v-for="t in r.tags" :key="t" size="small" color="gray">{{ t }}</a-tag></div>
        <div class="resource-footer"><span class="source-note" :title="sourceNotice(r)">{{ sourceNotice(r) }}</span><a-button type="text" size="mini" @click="emit('detail',r)">查看详情 <Icon icon="lucide:arrow-up-right" width="14" /></a-button><a-dropdown position="br" trigger="click"><a-button type="text" size="mini" :aria-label="r.name+'的更多操作'"><Icon icon="lucide:ellipsis" width="18" /></a-button><template #content><a-doption @click="emit('edit',r)">编辑资源</a-doption><a-doption :disabled="busy||(!r.enabled&&r.connection_sync&&r.connection_sync.state!=='synced')" @click="emit('toggle',r)">{{ r.enabled?'停用资源':'启用资源' }}</a-doption><a-doption class="danger-action" @click="emit('remove',r)">删除资源</a-doption></template></a-dropdown></div>
      </article>
    </div>
    <a-table v-else :data="shown" :pagination="false" :scroll="{x:900}" row-key="id">
      <template #columns>
        <a-table-column title="资源" :width="270"><template #cell="{record:r}"><div class="table-resource"><ResourceIcon :type="r.type" :size="28" /><div><a-link @click="emit('detail',r)">{{ r.name }}</a-link><small>{{ resourceProduct(r.type).name }}</small></div></div></template></a-table-column>
        <a-table-column title="连接地址"><template #cell="{record:r}"><span>{{ r.host }}:{{ r.port }}</span><small class="table-database">{{ r.database }}</small></template></a-table-column>
        <a-table-column title="环境"><template #cell="{record:r}"><span class="environment-badge">{{ environmentLabel(r.environment) }}</span></template></a-table-column>
        <a-table-column title="项目 / 标签"><template #cell="{record:r}">{{ [...(r.project_groups||[]),...(r.tags||[])].join(' · ')||'—' }}</template></a-table-column>
        <a-table-column title="状态" :width="100"><template #cell="{record:r}"><span class="resource-status" :class="{enabled:r.enabled}"><i />{{ r.enabled?'已启用':'未启用' }}</span></template></a-table-column>
        <a-table-column title="操作" :width="140" fixed="right"><template #cell="{record:r}"><a-space size="mini"><a-button type="text" size="mini" @click="emit('detail',r)">详情</a-button><a-button type="text" size="mini" @click="emit('edit',r)">编辑</a-button></a-space></template></a-table-column>
      </template>
    </a-table>
    <a-empty v-if="!filtered.length&&view==='cards'" description="没有符合条件的资源" />
    <a-pagination v-model:current="page" :total="filtered.length" :page-size="12" show-total />
  </div>
</template>

<style scoped>
.catalog{--catalog-border:var(--color-border-2);color:var(--color-text-1)}.catalog small{font-size:12px;color:var(--color-text-3)}.catalog-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:22px}.catalog-summary>div{display:flex;align-items:center;gap:14px;background:var(--color-bg-1);border:1px solid var(--catalog-border);border-radius:8px;padding:18px 20px}.catalog-summary b{display:block;font-size:25px;font-weight:600;line-height:1.15;margin-bottom:6px}.catalog-summary div>span:not(.summary-icon){color:var(--color-text-3);font-size:12px}.summary-icon{display:grid;place-items:center;width:44px;height:44px;border-radius:12px;color:#3267df;background:#edf3ff}.summary-icon.green{color:#239469;background:#eaf8f2}.summary-icon.violet{color:#8060cd;background:#f2edfc}.summary-icon.amber{color:#b88627;background:#fdf5e5}.environment-panel{background:var(--color-bg-1);border:1px solid var(--catalog-border);border-radius:8px;padding:18px 20px;margin-bottom:18px}.environment-heading{display:flex;gap:18px;align-items:baseline;margin-bottom:16px}.environment-heading>span{font-weight:500}.environment-tabs{display:flex;gap:8px;overflow:auto;padding-bottom:2px}.environment-tabs button{font:inherit;font-size:13px;display:inline-flex;align-items:center;gap:8px;border:1px solid transparent;border-radius:6px;background:var(--color-fill-1);color:var(--color-text-2);padding:9px 12px;white-space:nowrap;cursor:pointer}.environment-tabs button:hover{background:var(--color-fill-2)}.environment-tabs button.active{background:var(--color-primary-light-1);border-color:rgb(var(--primary-3));color:rgb(var(--primary-6))}.environment-tabs button>span:last-child{font-size:11px;background:var(--color-bg-1);border-radius:4px;min-width:20px;padding:2px 4px}.environment-dot{width:6px;height:6px;border-radius:50%;background:#7295cc}.environment-dot[data-environment=prod],.environment-dot[data-environment=production]{background:#e59649}.environment-dot[data-environment=dev]{background:#4aa889}.catalog-filter{display:grid;grid-template-columns:minmax(220px,2fr) repeat(4,minmax(100px,1fr)) auto;gap:10px;align-items:center}.catalog-bar{display:flex;align-items:center;justify-content:space-between;margin:24px 0 14px}.catalog-bar>div{display:flex;align-items:baseline;gap:12px}.catalog-bar b{font-weight:500}.resource-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px}.resource-card{min-width:0;display:flex;flex-direction:column;border:1px solid var(--catalog-border);border-radius:10px;background:var(--color-bg-1);padding:20px 20px 0;transition:border-color .15s,box-shadow .15s}.resource-card:hover{border-color:rgb(var(--primary-3));box-shadow:0 4px 18px #10275008}.resource-title{display:flex;gap:12px;align-items:center;margin-bottom:22px}.product-tile{display:grid;place-items:center;flex-shrink:0;width:48px;height:48px;background:var(--color-fill-1);border-radius:12px}.resource-title>div{flex:1;min-width:0}.resource-title small{display:block;margin-top:6px}.resource-name{border:0;padding:0;background:transparent;color:var(--color-text-1);text-align:left;font:inherit;font-weight:500;cursor:pointer;overflow-wrap:anywhere}.resource-name:hover{color:rgb(var(--primary-6))}.resource-status{display:inline-flex;align-items:center;gap:5px;flex-shrink:0;color:var(--color-text-3);font-size:11px;white-space:nowrap}.resource-status i{width:5px;height:5px;background:var(--color-text-4);border-radius:50%}.resource-status.enabled{color:#24885d}.resource-status.enabled i{background:#35a56f}.resource-address,.resource-database{display:flex;align-items:center;gap:9px;color:var(--color-text-2);font-size:12px;line-height:1.8;overflow-wrap:anywhere}.resource-address svg,.resource-database svg{flex-shrink:0;color:var(--color-text-4)}.resource-database{margin-top:3px}.resource-meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:16px 0 18px;min-height:22px}.environment-badge{display:inline-flex;align-items:center;gap:5px;color:#5a6e92;background:#f0f4fa;padding:4px 7px;border-radius:4px;font-size:11px;white-space:nowrap}.resource-meta :deep(.arco-tag){font-size:11px}.resource-footer{display:flex;align-items:center;gap:6px;margin-top:auto;padding:12px 0;border-top:1px solid var(--color-border-1)}.source-note{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--color-text-4);font-size:11px}.resource-footer :deep(.arco-btn){font-size:12px;padding:0 4px;gap:4px}.danger-action{color:rgb(var(--danger-6))}.table-resource{display:flex;align-items:center;gap:12px}.table-resource small,.table-database{display:block;margin-top:5px}.catalog>.arco-pagination{justify-content:flex-end;margin:22px 0}.catalog>.arco-empty{padding:48px}.catalog :deep(.arco-table-th){font-weight:500}
@media(min-width:1500px){.resource-grid{grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}}@media(max-width:1150px){.catalog-filter{grid-template-columns:2fr 1fr 1fr}.catalog-summary>div{padding:16px 12px;gap:10px}}@media(max-width:800px){.catalog-summary{grid-template-columns:1fr 1fr}.catalog-filter{grid-template-columns:1fr 1fr}.environment-heading{display:block}.environment-heading small{display:block;margin-top:6px}.resource-grid{grid-template-columns:1fr}}@media(max-width:450px){.catalog-filter{grid-template-columns:1fr}.catalog-summary>div{gap:8px}.summary-icon{display:none}.resource-card{padding:16px 14px 0}.catalog-bar>div{gap:6px}}
</style>
