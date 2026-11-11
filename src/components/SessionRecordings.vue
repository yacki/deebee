<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { Icon } from '@iconify/vue';
import { API_BASE, api, download } from '../api';
import { elapsed, useRecordingPlayer, type Recording } from '../recordings/player';
import PlaybackControls from './PlaybackControls.vue';
type Listing = { items:Recording[]; total:number; storage:{used_bytes:number; quota_bytes:number; session_limit_bytes:number; retention_days:number} };
const rows=ref<Recording[]>([]), total=ref(0), offset=ref(0), storage=ref<Listing['storage']>();
const query=ref(''), protocol=ref(''), status=ref(''), after=ref(''), before=ref(''), error=ref(''), loading=ref(false);
const {selected,viewport,loading:opening,error:playerError,playing,seeking,position,duration,speed,open,close,play,pause,seek,changeSpeed}=useRecordingPlayer();
let listGeneration=0, poll:ReturnType<typeof setInterval>|undefined;
const labels:Record<string,string>={recording:'录制中',completed:'已完成',failed:'异常结束',interrupted:'服务中断'};
const colors:Record<string,string>={recording:'blue',completed:'green',failed:'red',interrupted:'orange'};
function bytes(n:number){return n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KiB`:`${(n/1048576).toFixed(1)} MiB`;}
function time(n:number){return new Date(n*1000).toLocaleString();}
async function refresh(){
  const requestId=++listGeneration;loading.value=true;error.value='';
  const params=new URLSearchParams({q:query.value,protocol:protocol.value,status:status.value,offset:String(offset.value),limit:'20'});
  if(after.value)params.set('after',String(new Date(after.value).getTime()/1000));
  if(before.value)params.set('before',String(new Date(before.value).getTime()/1000));
  try{const result=await api<Listing>(`/admin/v1/session-recordings?${params}`);if(requestId!==listGeneration)return;rows.value=result.items;total.value=result.total;storage.value=result.storage;}
  catch(e){if(requestId===listGeneration)error.value=e instanceof Error?e.message:'无法读取录像';}
  finally{if(requestId===listGeneration)loading.value=false;}
}
function search(){offset.value=0;void refresh();}
function page(value:number){offset.value=(value-1)*20;void refresh();}
async function save(row:Recording){try{await download(`${API_BASE}/admin/v1/session-recordings/${row.id}/download`,`${row.id}.${row.protocol==='ssh'?'cast':'guac'}.gz`);}catch(e){error.value=e instanceof Error?e.message:'下载失败';}}

onMounted(()=>{void refresh();poll=setInterval(()=>{if(!selected.value&&!loading.value)void refresh();},10000);});
onBeforeUnmount(()=>{listGeneration++;if(poll)clearInterval(poll);});
defineExpose({refresh});
</script>
<template>
  <section class="recordings-workspace" aria-label="会话录像">
    <a-alert v-if="storage" type="info" role="status"><div class="recording-storage"><span>已用 {{ bytes(storage.used_bytes) }} / {{ bytes(storage.quota_bytes) }}</span><span>{{ storage.retention_days>0?`保留 ${storage.retention_days} 天`:'长期保留' }} · 单次上限 {{ bytes(storage.session_limit_bytes) }}</span></div></a-alert>
    <a-form :model="{query,protocol,status,after,before}" class="recording-filters" layout="vertical" @submit-success="search">
      <a-form-item label="服务器 / 用户"><a-input v-model="query" :input-attrs="{'aria-label':'服务器 / 用户'}" placeholder="地址、名称、操作人或登录账号" allow-clear /></a-form-item>
      <a-form-item label="协议"><a-select v-model="protocol" aria-label="协议" role="combobox" :options="[{value:'',label:'全部协议'},{value:'ssh',label:'SSH'},{value:'rdp',label:'RDP'}]" /></a-form-item>
      <a-form-item label="状态"><a-select v-model="status" aria-label="状态" role="combobox" :options="[{value:'',label:'全部状态'},...Object.entries(labels).map(([value,label])=>({value,label}))]" /></a-form-item>
      <a-form-item label="开始时间"><a-date-picker v-model="after" show-time value-format="YYYY-MM-DDTHH:mm:ss" aria-label="开始时间" /></a-form-item>
      <a-form-item label="结束时间"><a-date-picker v-model="before" show-time value-format="YYYY-MM-DDTHH:mm:ss" aria-label="结束时间" /></a-form-item>
      <a-button html-type="submit" type="primary" :loading="loading"><template #icon><Icon icon="lucide:search" /></template>查询</a-button>
    </a-form>
    <a-alert v-if="error" type="error" role="alert">{{ error }}</a-alert>
    <section v-if="selected" :key="selected.id" class="recording-player" aria-label="录像播放器">
      <header><div><strong>{{ selected.protocol.toUpperCase() }} · {{ selected.target }}</strong><small>{{ selected.actor }} → {{ selected.username }}@{{ selected.host }} · {{ time(selected.started_at) }}</small></div><a-tooltip content="关闭回放"><IconButton shape="circle" label="关闭回放" @click="close"><Icon icon="lucide:x" /></IconButton></a-tooltip></header>
      <a-alert v-if="selected.error" type="warning">{{ selected.error }}；可回放已保存部分。</a-alert>
      <a-alert v-if="playerError" type="error" role="alert">{{ playerError }}</a-alert>
      <a-spin :loading="opening" tip="正在加载录像…" class="recording-stage"><span v-if="opening" role="status" class="recording-loading">正在加载录像…</span><div ref="viewport" class="recording-viewport" :class="selected.protocol" /></a-spin>
      <PlaybackControls :position="position" :duration="duration" :playing="playing" :loading="opening" :seeking="seeking" :error="playerError" :protocol="selected.protocol" :speed="speed" @play="play" @pause="pause" @seek="seek" @speed="changeSpeed" />
    </section>
    <a-table class="recordings-table" :data="rows" row-key="id" :loading="loading" :pagination="false" :scroll="{x:1000}">
      <template #columns>
        <a-table-column title="服务器"><template #cell="{record:row}"><strong>{{ row.target }}</strong><small>{{ row.protocol.toUpperCase() }} · {{ row.host }}</small></template></a-table-column>
        <a-table-column title="操作人 / 登录账号"><template #cell="{record:row}">{{ row.actor }}<small>{{ row.username }}</small></template></a-table-column>
        <a-table-column title="开始时间"><template #cell="{record:row}">{{ time(row.started_at) }}</template></a-table-column>
        <a-table-column title="时长"><template #cell="{record:row}">{{ elapsed(row.duration*1000) }}</template></a-table-column>
        <a-table-column title="大小"><template #cell="{record:row}">{{ bytes(row.stored_bytes) }}</template></a-table-column>
        <a-table-column title="状态"><template #cell="{record:row}"><a-tag :color="colors[row.status]">{{ labels[row.status]||row.status }}</a-tag><small v-if="row.error">{{ row.error }}</small></template></a-table-column>
        <a-table-column title="操作" :width="180"><template #cell="{record:row}"><a-space><a-button type="text" :disabled="row.status==='recording'||!row.stored_bytes" @click="open(row)"><template #icon><Icon icon="lucide:play" /></template>回放</a-button><a-tooltip content="下载录像"><IconButton shape="circle" label="下载" :disabled="row.status==='recording'||!row.stored_bytes" @click="save(row)"><Icon icon="lucide:download" /></IconButton></a-tooltip></a-space></template></a-table-column>
      </template>
      <template #empty><a-empty description="暂无录像。通过工作台打开 SSH 或 RDP 会话后，将自动开始录制。" /></template>
    </a-table>
    <a-pagination :current="Math.floor(offset/20)+1" :total="total" :page-size="20" show-total @change="page" />
  </section>
</template>
<style scoped>
.recordings-workspace{display:flex;flex-direction:column;gap:18px;min-width:0}.recording-storage{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}.recording-filters{display:flex;flex-direction:row;width:100%;gap:12px;align-items:flex-end;flex-wrap:wrap}.recording-filters :deep(.arco-form-item){width:155px;margin:0}.recording-filters :deep(.arco-form-item:first-child){flex:1;min-width:200px}.recording-filters :deep(.arco-picker){width:100%}.recordings-table :deep(small){display:block;margin-top:6px;color:var(--color-text-3);overflow-wrap:anywhere}.recording-player{border:1px solid var(--color-border-2);border-radius:var(--border-radius-medium);overflow:hidden;background:var(--color-bg-1)}.recording-player header{display:flex;align-items:center;gap:16px;padding:14px 18px}.recording-player header>div{flex:1}.recording-player header small{display:block;margin-top:6px;color:var(--color-text-3)}.recording-stage{display:block;width:100%;position:relative}.recording-loading{position:absolute;left:16px;top:12px;color:var(--color-text-3);font-size:var(--deebee-font-size)}.recording-viewport{background:#0b1220;min-height:240px;max-height:65vh;overflow:auto;width:100%}.recording-viewport.rdp{display:flex;justify-content:center;overflow:hidden}.recording-viewport.ssh{padding:12px}.recordings-workspace>.arco-pagination{align-self:flex-end}header svg{width:18px;height:18px}
</style>
