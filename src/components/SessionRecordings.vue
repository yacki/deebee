<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import Guacamole from 'guacamole-common-js';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { API_BASE, api, authToken, download } from '../api';

type Recording = { id:string; protocol:'ssh'|'rdp'; actor:string; target:string; host:string; username:string; started_at:number; duration:number; status:string; raw_bytes:number; stored_bytes:number; error:string; sha256:string };
type Listing = { items:Recording[]; total:number; storage:{used_bytes:number; quota_bytes:number; session_limit_bytes:number; retention_days:number} };
type CastEvent = [number,string,string];
const rows = ref<Recording[]>([]), total = ref(0), offset = ref(0);
const storage = ref<Listing['storage']>();
const query = ref(''), protocol = ref(''), status = ref(''), after = ref(''), before = ref('');
const error = ref(''), playerError = ref(''), loading = ref(false), opening = ref(false);
const selected = ref<Recording>(), viewport = ref<HTMLElement>();
const playing = ref(false), position = ref(0), duration = ref(0), speed = ref(1), seeking = ref(false);
let recording:Guacamole.SessionRecording|undefined, terminal:Terminal|undefined, observer:ResizeObserver|undefined;
let rendering = false, sshRenderGeneration = 0;
let events:CastEvent[] = [], eventIndex = 0, initialSize = {cols:120,rows:32};
let playbackTimer:ReturnType<typeof setInterval>|undefined, poll:ReturnType<typeof setInterval>|undefined;
let controller:AbortController|undefined, generation = 0, listGeneration = 0, clockStart = 0, startPosition = 0;
const labels:Record<string,string> = {recording:'录制中',completed:'已完成',failed:'异常结束',interrupted:'服务中断'};
function bytes(n:number){return n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KiB`:`${(n/1048576).toFixed(1)} MiB`;}
function time(n:number){return new Date(n*1000).toLocaleString();}
function elapsed(n:number){const s=Math.floor(n/1000);return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`;}
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
function page(delta:number){offset.value=Math.max(0,offset.value+delta*20);void refresh();}
function pause(){recording?.pause();if(playbackTimer)clearInterval(playbackTimer);playbackTimer=undefined;playing.value=false;}
function scale(){if(!recording||!viewport.value)return;const display=recording.getDisplay();if(display.getWidth()>0)display.scale(Math.min(1,viewport.value.clientWidth/display.getWidth(),Math.max(240,window.innerHeight*0.5)/Math.max(1,display.getHeight())));}
function destroy(){generation++;rendering=false;sshRenderGeneration++;controller?.abort();controller=undefined;pause();recording?.abort();recording=undefined;terminal?.dispose();terminal=undefined;observer?.disconnect();observer=undefined;events=[];eventIndex=0;viewport.value?.replaceChildren();position.value=0;duration.value=0;seeking.value=false;}
function close(){destroy();selected.value=undefined;}
function write(data:string){return new Promise<void>(resolve=>terminal?terminal.write(data,resolve):resolve());}
async function renderSsh(target:number,reset=false){
  if(!terminal)return;
  if(!reset&&rendering)return;
  if(reset)sshRenderGeneration++;
  const renderGeneration=sshRenderGeneration;rendering=true;
  const active=generation;
  try{
  if(reset){terminal.reset();terminal.resize(initialSize.cols,initialSize.rows);eventIndex=0;}
  let batch='';
  while(eventIndex<events.length&&events[eventIndex]![0]*1000<=target){
    if(active!==generation||renderGeneration!==sshRenderGeneration)return;
    const e=events[eventIndex++]!;
    if(e[1]==='o')batch+=e[2];
    else if(e[1]==='r'){
      if(batch){await write(batch);batch='';}
      if(active!==generation||renderGeneration!==sshRenderGeneration)return;
      const [cols,rows]=e[2].split('x').map(Number);if(cols&&rows)terminal?.resize(cols,rows);
    }
    if(batch.length>=65536){await write(batch);batch='';}
  }
  if(batch)await write(batch);
  }finally{rendering=false;}
}
function play(){
  if(playing.value||opening.value||seeking.value)return;
  if(recording){if(position.value>=duration.value)recording.seek(0,()=>recording?.play());else recording.play();return;}
  if(!terminal)return;
  if(position.value>=duration.value){void seek(0).then(play);return;}
  playing.value=true;clockStart=performance.now();startPosition=position.value;
  playbackTimer=setInterval(()=>{const target=Math.min(duration.value,startPosition+(performance.now()-clockStart)*speed.value);position.value=target;void renderSsh(target);if(target>=duration.value)pause();},50);
}
async function seek(target:number){
  const active=generation;
  const resume=playing.value;pause();seeking.value=true;
  if(recording){recording.seek(target,()=>{if(active!==generation)return;seeking.value=false;position.value=target;if(resume&&target<duration.value)play();});}
  else{await renderSsh(target,true);if(active!==generation)return;position.value=target;seeking.value=false;if(resume&&target<duration.value)play();}
}
function seekInput(event:Event){void seek(Number((event.target as HTMLInputElement).value));}
function changeSpeed(){if(playing.value){pause();play();}}
async function open(row:Recording){
  destroy();selected.value=row;playerError.value='';opening.value=true;speed.value=1;
  const active=generation;controller=new AbortController();
  try{
    await nextTick();if(!viewport.value||active!==generation)return;
    if(row.protocol==='rdp'){
      const tunnel=new Guacamole.StaticHTTPTunnel(`${API_BASE}/admin/v1/session-recordings/${row.id}/content`,false,{Authorization:`Bearer ${authToken()}`});
      const player=new Guacamole.SessionRecording(tunnel);recording=player;
      viewport.value.appendChild(player.getDisplay().getElement());
      player.getDisplay().onresize=scale;observer=new ResizeObserver(scale);observer.observe(viewport.value);
      player.onprogress=ms=>{if(active===generation)duration.value=ms;};
      player.onseek=ms=>{if(active===generation){position.value=ms;scale();}};
      player.onplay=()=>{if(active===generation)playing.value=true;};player.onpause=()=>{if(active===generation)playing.value=false;};
      player.onerror=message=>{if(active===generation){playerError.value=message;opening.value=false;}};
      player.onload=()=>{if(active!==generation)return;duration.value=player.getDuration();opening.value=false;player.play();};
      player.connect();
    }else{
      const response=await fetch(`${API_BASE}/admin/v1/session-recordings/${row.id}/content`,{headers:{Authorization:`Bearer ${authToken()}`},signal:controller.signal});
      if(!response.ok)throw new Error((await response.json()).error?.message||'无法读取录像');
      const lines=(await response.text()).trim().split('\n');if(active!==generation)return;
      const header=JSON.parse(lines.shift()||'{}');
      if(header.version!==2)throw new Error('不支持的终端录像格式');
      initialSize={cols:header.width||120,rows:header.height||32};
      events=lines.filter(Boolean).map(line=>JSON.parse(line) as CastEvent);
      duration.value=Math.max(row.duration*1000,(events.at(-1)?.[0]||0)*1000,1);
      terminal=new Terminal({cols:initialSize.cols,rows:initialSize.rows,fontSize:13,disableStdin:true,cursorBlink:false,scrollback:3000,theme:{background:'#0b1220',foreground:'#d9e2f2'}});
      terminal.open(viewport.value);opening.value=false;play();
    }
  }catch(e){if(active===generation){playerError.value=e instanceof Error?e.message:'录像加载失败';opening.value=false;}}
}
async function save(row:Recording){try{await download(`${API_BASE}/admin/v1/session-recordings/${row.id}/download`,`${row.id}.${row.protocol==='ssh'?'cast':'guac'}.gz`);}catch(e){error.value=e instanceof Error?e.message:'下载失败';}}
onMounted(()=>{void refresh();poll=setInterval(()=>{if(!selected.value)void refresh();},10000);});
onBeforeUnmount(()=>{listGeneration++;if(poll)clearInterval(poll);destroy();});
defineExpose({refresh});
</script>

<template>
  <section class="recordings-workspace" aria-label="会话录像">
    <div v-if="storage" class="recording-storage"><span>已用 {{ bytes(storage.used_bytes) }} / {{ bytes(storage.quota_bytes) }}</span><span>{{ storage.retention_days>0?`保留 ${storage.retention_days} 天`:'长期保留' }} · 单次上限 {{ bytes(storage.session_limit_bytes) }}</span></div>
    <form class="recording-filters" @submit.prevent="search">
      <label>服务器 / 用户<input v-model="query" placeholder="地址、名称、操作人或登录账号" /></label>
      <label>协议<select v-model="protocol" aria-label="协议"><option value="">全部协议</option><option value="ssh">SSH</option><option value="rdp">RDP</option></select></label>
      <label>状态<select v-model="status" aria-label="状态"><option value="">全部状态</option><option v-for="(label,value) in labels" :key="value" :value="value">{{ label }}</option></select></label>
      <label>开始时间<input v-model="after" type="datetime-local" /></label><label>结束时间<input v-model="before" type="datetime-local" /></label>
      <button type="submit" :disabled="loading">查询</button>
    </form>
    <p v-if="error" role="alert" class="recording-error">{{ error }}</p>
    <section v-if="selected" class="recording-player" aria-label="录像播放器">
      <header><strong>{{ selected.protocol.toUpperCase() }} · {{ selected.target }}</strong><span>{{ selected.actor }} → {{ selected.username }}@{{ selected.host }} · {{ time(selected.started_at) }}</span><button @click="close">关闭回放</button></header>
      <p v-if="selected.error" class="recording-error">{{ selected.error }}；可回放已保存部分。</p>
      <p v-if="playerError" role="alert" class="recording-error">{{ playerError }}</p>
      <p v-if="opening" role="status">正在加载录像…</p>
      <div ref="viewport" class="recording-viewport" :class="selected.protocol" />
      <footer>
        <button :disabled="opening||seeking||!!playerError" @click="playing?pause():play()">{{ playing?'暂停':'播放' }}</button>
        <input aria-label="回放进度" type="range" min="0" :max="Math.max(duration,1)" :value="position" :disabled="opening||seeking" @change="seekInput" />
        <output>{{ elapsed(position) }} / {{ elapsed(duration) }}</output>
        <select v-if="selected.protocol==='ssh'" v-model="speed" aria-label="回放速度" @change="changeSpeed"><option :value="0.5">0.5 倍</option><option :value="1">1 倍</option><option :value="2">2 倍</option><option :value="4">4 倍</option></select>
        <button :disabled="opening||seeking" @click="seek(duration)">跳至结尾</button>
      </footer>
    </section>
    <div class="recordings-table-wrap">
      <table class="recordings-table">
        <thead><tr><th>服务器</th><th>操作人 / 登录账号</th><th>开始时间</th><th>时长</th><th>大小</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id"><td><strong>{{ row.target }}</strong><small>{{ row.protocol.toUpperCase() }} · {{ row.host }}</small></td><td>{{ row.actor }}<small>{{ row.username }}</small></td><td>{{ time(row.started_at) }}</td><td>{{ elapsed(row.duration*1000) }}</td><td>{{ bytes(row.stored_bytes) }}</td><td><span :class="['recording-status',row.status]">{{ labels[row.status]||row.status }}</span><small v-if="row.error">{{ row.error }}</small></td><td><button :disabled="row.status==='recording'||!row.stored_bytes" @click="open(row)">回放</button><button :disabled="row.status==='recording'||!row.stored_bytes" @click="save(row)">下载</button></td></tr>
          <tr v-if="!rows.length"><td colspan="7" class="recordings-empty">{{ loading?'正在读取录像…':'暂无录像。通过工作台打开 SSH 或 RDP 会话后，将自动开始录制。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="recording-pagination"><span>共 {{ total }} 条录像</span><button :disabled="offset===0||loading" @click="page(-1)">上一页</button><span>第 {{ Math.floor(offset/20)+1 }} 页</span><button :disabled="offset+20>=total||loading" @click="page(1)">下一页</button></div>
  </section>
</template>

<style scoped>
.recordings-workspace{display:flex;flex-direction:column;gap:18px}.recording-storage,.recording-pagination{display:flex;align-items:center;gap:16px;color:#64748b}.recording-storage{justify-content:space-between;padding:14px 18px;background:#eef4fb;border:1px solid #dce7f5;border-radius:8px}.recording-filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px}.recording-filters label{display:flex;flex-direction:column;gap:6px;font-size:12px;color:#475569}.recording-filters label:first-child{flex:1;min-width:180px}input,select,button{font:inherit}input:not([type=range]),select{padding:8px 10px;border:1px solid #cbd5e1;border-radius:5px;background:white;color:#243348;min-height:35px}button{border:1px solid #b8cde5;background:#f8fbff;border-radius:5px;color:#235a96;padding:7px 12px;cursor:pointer}button:disabled{opacity:.45;cursor:default}.recordings-table-wrap{overflow:auto;background:white;border:1px solid #e2e8f0;border-radius:8px}.recordings-table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}.recordings-table th{background:#f7f9fc;color:#64748b;font-weight:500}.recordings-table td,.recordings-table th{padding:14px 12px;border-bottom:1px solid #edf1f7;vertical-align:top}.recordings-table td:last-child{white-space:nowrap}.recordings-table td button+button{margin-left:8px}.recordings-table small{display:block;margin-top:5px;color:#7a8798;max-width:220px;word-break:break-word}.recordings-empty{text-align:center!important;padding:36px!important;color:#7a8798}.recording-status{color:#64748b;white-space:nowrap}.recording-status.recording{color:#cf3451}.recording-status.completed{color:#178756}.recording-status.failed,.recording-status.interrupted,.recording-error{color:#b84a35}.recording-player{background:white;border:1px solid #cbd5e1;border-radius:8px;overflow:hidden}.recording-player header,.recording-player footer{display:flex;align-items:center;gap:12px;padding:12px 16px}.recording-player header{flex-wrap:wrap;background:#f5f8fc}.recording-player header>span{flex:1;color:#64748b;font-size:12px}.recording-player p{padding:0 16px}.recording-viewport{background:#0b1220;min-height:240px;max-height:65vh;overflow:auto;width:100%}.recording-viewport.rdp{display:flex;justify-content:center;min-height:240px;overflow:hidden}.recording-viewport.ssh{padding:10px}.recording-player footer input{flex:1;min-width:40px}.recording-player output{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}.recording-pagination{justify-content:flex-end}.recording-pagination>span:first-child{margin-right:auto}@media(max-width:900px){.recording-filters label{flex:1;min-width:140px}.recording-player footer{flex-wrap:wrap}.recording-storage{align-items:flex-start;flex-direction:column;gap:6px}}
</style>
