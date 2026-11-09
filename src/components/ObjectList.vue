<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { DbObject, Objects, ObjectsTab } from "../types";
const props = defineProps<{ tab: ObjectsTab; objects?: Objects }>();
const emit = defineEmits<{ openTable: [name: string]; designTable: [name: string]; openObject: [item: DbObject]; menu: [event: MouseEvent, item: DbObject] }>();
const filter = ref("");
const items = computed(() => (props.objects?.[props.tab.kind] || []).filter(item => item.name.toLowerCase().includes(filter.value.toLowerCase())).map(item=>({...item,key:String(item.object_id||`${item.name}(${item.identity_arguments||""})`)})));
const storageKey = computed(() => `deebee_object_widths:${props.tab.profileId}:${props.tab.database}:${props.tab.schema}:${props.tab.kind}`);
const widths = ref<Record<string, number>>({});
const defaults = [
  {dataIndex:"name",title:"名称",width:240,slotName:"name",fixed:"left" as const},
  {dataIndex:"estimated_rows",title:"行数",width:88},
  {dataIndex:"data_length",title:"数据大小",width:112,slotName:"dataLength"},
  {dataIndex:"index_length",title:"索引大小",width:112,slotName:"indexLength"},
  {dataIndex:"engine",title:"引擎",width:112},
  {dataIndex:"created_at",title:"创建时间",width:184},
  {dataIndex:"updated_at",title:"更新时间",width:184},
  {dataIndex:"collation",title:"排序规则",width:240},
  {dataIndex:"comment",title:"注释",width:360,titleSlotName:"commentTitle"},
];
const columns = computed(() => defaults.map(column => ({...column,width:widths.value[column.dataIndex]||column.width,ellipsis:true,tooltip:true})));
const tableWidth = computed(() => columns.value.reduce((sum,column)=>sum+column.width,0));
const container = ref<HTMLElement>();
const bodyHeight = ref(400);
let observer: ResizeObserver | undefined;
watch(storageKey, () => {
  try { const saved=JSON.parse(localStorage.getItem(storageKey.value)||'{}');widths.value=Object.fromEntries(Object.entries(saved).filter(([key,value])=>defaults.some(column=>column.dataIndex===key)&&typeof value==='number'&&Number.isFinite(value)&&value>=40&&value<=2000)) as Record<string,number>; }
  catch { widths.value={}; }
}, {immediate:true});
function resizeColumn(name: string, width: number) {
  const body=container.value?.querySelector<HTMLElement>(".arco-table-body");
  const pinnedToEnd=body&&body.scrollWidth-body.clientWidth-body.scrollLeft<2;
  widths.value={...widths.value,[name]:width};
  localStorage.setItem(storageKey.value,JSON.stringify(widths.value));
  if(name==="comment"&&body&&pinnedToEnd)void nextTick(()=>{body.scrollLeft=body.scrollWidth;});
}
let stopLastResize: (()=>void) | undefined;
function resizeLastColumn(event: PointerEvent) {
  event.preventDefault();event.stopPropagation();stopLastResize?.();
  const start=event.clientX,initial=(event.currentTarget as HTMLElement).closest('th')?.getBoundingClientRect().width||360;
  const move=(next:PointerEvent)=>resizeColumn('comment',Math.max(40,Math.min(2000,Math.round(initial+next.clientX-start))));
  const stop=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',stop);document.removeEventListener('pointercancel',stop);stopLastResize=undefined;};
  stopLastResize=stop;document.addEventListener('pointermove',move);document.addEventListener('pointerup',stop);document.addEventListener('pointercancel',stop);
}
function open(item: DbObject) { if(props.tab.kind==='tables')emit('openTable',item.name);else emit('openObject',item); }
function bytes(value?: number) { if (value == null) return "—"; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; }
onMounted(()=>{
  observer=new ResizeObserver(([entry])=>{if(entry)bodyHeight.value=Math.max(80,Math.floor(entry.contentRect.height)-40);});
  if(container.value)observer.observe(container.value);
});
onBeforeUnmount(()=>{observer?.disconnect();stopLastResize?.();});
</script>
<template>
  <section class="object-list-view">
    <header class="view-header"><div><Icon icon="lucide:folder-tree" /><span><strong>{{ tab.title }}</strong><small>{{ tab.database }}{{ tab.schema ? ` · ${tab.schema}` : '' }}</small></span></div><WorkbenchInput v-model="filter" class="object-filter" aria-label="筛选对象" placeholder="筛选对象"><template #prefix><Icon icon="lucide:search" /></template></WorkbenchInput></header>
    <div ref="container" class="object-table">
      <a-table :key="storageKey" :data="items" :columns="columns" row-key="key" :pagination="false" :scroll="{x:tableWidth,y:bodyHeight}" column-resizable :bordered="{cell:true}" size="small" @column-resize="resizeColumn" @row-dblclick="item=>open(item as DbObject)" @row-contextmenu="(item,event)=>{event.preventDefault();emit('menu',event as MouseEvent,item as DbObject)}">
        <template #commentTitle>注释<span class="object-column-handle" role="separator" aria-label="调整对象注释列宽" aria-orientation="vertical" tabindex="0" :aria-valuenow="widths.comment||360" aria-valuemin="40" aria-valuemax="2000" title="拖动调整注释列宽，双击恢复默认" @pointerdown="resizeLastColumn" @keydown.left.prevent="resizeColumn('comment',Math.max(40,(widths.comment||360)-16))" @keydown.right.prevent="resizeColumn('comment',Math.min(2000,(widths.comment||360)+16))" @dblclick.stop="resizeColumn('comment',360)" /></template>
        <template #name="{record:item}"><span class="object-name"><Icon :icon="tab.kind==='tables'?'lucide:table-2':'lucide:box'" />{{ item.name }}{{ item.identity_arguments!==undefined?`(${item.identity_arguments})`:'' }}</span></template>
        <template #dataLength="{record:item}">{{ bytes(item.data_length) }}</template>
        <template #indexLength="{record:item}">{{ bytes(item.index_length) }}</template>
      </a-table>
    </div>
  </section>
</template>
