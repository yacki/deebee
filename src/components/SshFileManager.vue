<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { Icon } from '@iconify/vue';
import { Button as AButton } from '@arco-design/web-vue';
import '@arco-design/web-vue/dist/arco.css';
import WorkbenchModal from './ui/WorkbenchModal.vue';
import { API_BASE, api, authToken, jsonBody } from '../api';
import type { Profile } from '../types';
import { confirmAction, promptValue } from './ui/dialogs';

type Entry = { name:string; path:string; is_dir:boolean; is_link:boolean; size:number; modified:number|null; permissions:string };
type Listing = { path:string; home:string; items:Entry[]; max_file_bytes:number };
type Transfer = { id:number; name:string; direction:string; progress:number; state:'running'|'done'|'error'|'cancelled'; detail:string };
type TreeRow = { path:string; label:string; depth:number; link?:boolean };
const props = defineProps<{ profile:Profile }>();
const emit = defineEmits<{ close:[] }>();
const base = `/connections/${encodeURIComponent(props.profile.id)}/files`;
const path = ref(''), address = ref(''), home = ref(''), entries = ref<Entry[]>([]);
const cache = ref<Record<string, Entry[]>>({}), expanded = ref(new Set<string>()), treeLoading = ref(new Set<string>());
const selected = ref(new Set<string>()), filter = ref(''), showHidden = ref(false), loading = ref(false), working = ref(false);
const error = ref(''), notice = ref(''), uploadInput = ref<HTMLInputElement>(), dropActive = ref(false);
const transfers = ref<Transfer[]>([]), sort = ref('name'), ascending = ref(true);
let navigation = 0, transferId = 0, disposed = false, abort: (()=>void)|undefined;
const parent = (value:string) => value.replace(/\/[^/]+\/?$/, '') || '/';
const join = (directory:string, name:string) => `${directory.replace(/\/$/,'')}/${name}`;
const message = (reason:unknown) => reason instanceof Error ? reason.message : String(reason);
const chosen = computed(() => entries.value.filter(item => selected.value.has(item.path)));
const visible = computed(() => entries.value.filter(item => (showHidden.value || !item.name.startsWith('.')) && item.name.toLowerCase().includes(filter.value.toLowerCase())).sort((a,b) => {
  if(a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
  const value = sort.value === 'size' ? a.size-b.size : sort.value === 'modified' ? (a.modified||0)-(b.modified||0) : a.name.localeCompare(b.name, 'zh', {numeric:true});
  return ascending.value ? value : -value;
}));
const allSelected = computed(() => visible.value.length > 0 && visible.value.every(item => selected.value.has(item.path)));
const tree = computed(() => {
  const rows:TreeRow[] = [];
  function branch(directory:string, label:string, depth:number, ancestors:Set<string>) {
    rows.push({path:directory, label, depth});
    if(!expanded.value.has(directory) || depth > 30) return;
    for(const item of cache.value[directory] || []) {
      if(!item.is_dir || (!showHidden.value && item.name.startsWith('.')) || ancestors.has(item.path)) continue;
      if(item.is_link) rows.push({path:item.path, label:item.name, depth:depth+1, link:true});
      else branch(item.path, item.name, depth+1, new Set([...ancestors, directory]));
    }
  }
  if(home.value && home.value !== '/') branch(home.value, '主目录', 0, new Set());
  branch('/', '文件系统 /', 0, new Set());
  return rows;
});
function size(value:number) {
  if(value < 1024) return `${value} B`;
  const unit = Math.min(3, Math.floor(Math.log(value)/Math.log(1024)));
  return `${(value/1024**unit).toFixed(1)} ${['B','KB','MB','GB'][unit]}`;
}
function date(value:number|null) { return value ? new Date(value*1000).toLocaleString('zh-CN',{hour12:false}) : '—'; }
async function listing(directory:string) {
  const result = await api<Listing>(`${base}?path=${encodeURIComponent(directory)}`);
  if(!disposed) cache.value[result.path] = result.items;
  return result;
}
async function navigate(directory:string) {
  if(working.value) return;
  const request = ++navigation;
  loading.value = true; error.value = ''; notice.value = '';
  try {
    const result = await listing(directory);
    if(disposed || request !== navigation) return;
    path.value = result.path; address.value = result.path; home.value = result.home;
    entries.value = result.items; selected.value = new Set(); filter.value = '';
    if(result.path === result.home) expanded.value.add(result.home);
  } catch(reason) { if(request === navigation && !disposed) error.value = message(reason); }
  finally { if(request === navigation) loading.value = false; }
}
async function toggle(directory:string) {
  if(expanded.value.has(directory)) { expanded.value.delete(directory); return; }
  treeLoading.value.add(directory);
  try { if(!cache.value[directory]) await listing(directory); expanded.value.add(directory); }
  catch(reason) { error.value = message(reason); }
  finally { treeLoading.value.delete(directory); }
}
function select(item:Entry, event?:MouseEvent) {
  if(event?.ctrlKey || event?.metaKey) {
    const next = new Set(selected.value); if(next.has(item.path)) next.delete(item.path); else next.add(item.path); selected.value = next;
  } else selected.value = new Set([item.path]);
}
function check(item:Entry) { const next = new Set(selected.value); if(next.has(item.path)) next.delete(item.path); else next.add(item.path); selected.value = next; }
function checkAll() { selected.value = allSelected.value ? new Set() : new Set(visible.value.map(item => item.path)); }
function changeSort(key:string) { if(sort.value === key) ascending.value = !ascending.value; else {sort.value = key; ascending.value = true;} }
async function refresh() {
  cache.value = {}; expanded.value = new Set();
  await navigate(path.value || '');
  if(home.value && home.value !== path.value) { try {await listing(home.value); expanded.value.add(home.value);} catch { /* Current folder remains available. */ } }
}
async function perform(action:string, source:string, target='', recursive=false) {
  return api(`${base}/actions`,{method:'POST', body:jsonBody({action, path:source, target, recursive})});
}
async function mutation(work:()=>Promise<void>, success:string) {
  if(working.value || loading.value) return;
  working.value = true; error.value = ''; notice.value = '';
  let completed = false;
  try { await work(); completed = true; }
  catch(reason) { error.value = message(reason); }
  finally { working.value = false; }
  // Reload after failures too: a batch may have partially completed.
  const operationError = error.value;
  await refresh();
  if(operationError) error.value = operationError;
  else if(completed) notice.value = success;
}
function validName(value:string) { if(!value || value === '.' || value === '..' || /[/\\\0]/.test(value)) {error.value = '名称不能为空，不能包含路径分隔符，也不能为 . 或 ..';return false;} return true; }
async function mkdir() {
  const value = await promptValue('新建文件夹名称');
  if(value === null || !validName(value)) return;
  const directory = path.value;
  await mutation(async()=>{await perform('mkdir',join(directory,value));}, '文件夹已创建');
}
async function rename() {
  const item = chosen.value[0]; if(!item) return;
  const value = await promptValue('重命名', item.name);
  if(value === null || value === item.name || !validName(value)) return;
  await mutation(async()=>{await perform('move',item.path,join(parent(item.path),value));}, '重命名已完成');
}
async function moveTo(directory?:string, items=chosen.value) {
  if(!items.length || working.value) return;
  const destination = directory ?? await promptValue('移动到远程目录（绝对路径）',path.value);
  if(destination === null) return;
  if(!destination.startsWith('/')) {error.value = '请输入以 / 开头的远程绝对路径';return;}
  const targets = [...items];
  if(!await confirmAction(`将 ${targets.length} 项移动到 ${destination}？同名文件不会被覆盖。`)) return;
  await mutation(async()=>{for(const item of targets) await perform('move',item.path,join(destination,item.name));}, `已移动 ${targets.length} 项`);
}
async function remove() {
  const items = [...chosen.value]; if(!items.length) return;
  if(!await confirmAction(`永久删除 ${items.length} 项（${items.map(item=>item.name).join('、')}）？文件夹内的所有内容也会删除，此操作无法撤销。`)) return;
  await mutation(async()=>{for(const item of items) await perform('delete',item.path,'',true);}, `已删除 ${items.length} 项`);
}
function newTransfer(name:string, direction:string) {
  const item:Transfer = {id:++transferId, name, direction, progress:0, state:'running', detail:''};
  transfers.value = [item,...transfers.value].slice(0,100);
  return transfers.value[0]!;
}
function sendFile(file:File, directory:string, transfer:Transfer) {
  return new Promise<void>((resolve,reject)=>{
    const xhr = new XMLHttpRequest();
    xhr.open('POST',`${API_BASE}${base}/upload?path=${encodeURIComponent(directory)}`);
    xhr.setRequestHeader('Authorization',`Bearer ${authToken()}`);
    xhr.timeout = 3600000;
    xhr.upload.onprogress = event => {if(event.lengthComputable) transfer.progress = Math.min(95,Math.round(event.loaded/event.total*95));};
    xhr.upload.onload = () => {transfer.detail = '正在写入远程文件…';};
    xhr.onload = () => {
      if(xhr.status >= 200 && xhr.status < 300) resolve();
      else {let detail = `上传失败 (${xhr.status})`;try {detail = JSON.parse(xhr.responseText).error?.message || detail;} catch { /* Non-JSON proxy errors. */ } reject(new Error(detail));}
    };
    xhr.onerror = () => reject(new Error('上传连接中断，请刷新目录核实远端状态'));
    xhr.ontimeout = () => reject(new Error('上传超时，请刷新目录核实远端状态'));
    xhr.onabort = () => {transfer.state = 'cancelled'; reject(new Error('已停止等待上传结果，请刷新目录核实远端状态'));};
    abort = () => xhr.abort();
    const data = new FormData(); data.append('file',file); xhr.send(data);
  });
}
async function upload(files:File[], directory=path.value) {
  if(!files.length || working.value || loading.value || !path.value) return;
  await mutation(async()=>{
    let failures = 0;
    for(const file of files) {
      if(disposed) break;
      const transfer = newTransfer(file.name,'上传');
      try {
        if(file.size > 2*1024**3) throw new Error('单个文件不能超过 2 GB');
        await sendFile(file,directory,transfer); transfer.state = 'done'; transfer.progress = 100; transfer.detail = '已完成';
      } catch(reason) {if(transfer.state !== 'cancelled') transfer.state = 'error'; transfer.detail = message(reason); failures++;}
      finally {abort = undefined;}
      if(transfer.state === 'cancelled') break;
    }
    if(failures) throw new Error('部分文件上传未完成，请查看传输记录');
  }, '上传完成');
}
function pickFiles(event:Event) { const input = event.target as HTMLInputElement; const files = Array.from(input.files||[]); input.value = ''; void upload(files); }
async function downloadSelected() {
  const items = chosen.value.filter(item=>!item.is_dir); if(!items.length) return;
  working.value = true; error.value = ''; notice.value = '';
  for(const item of items) {
    if(disposed) break;
    const transfer = newTransfer(item.name,'下载'), controller = new AbortController(); abort = () => controller.abort();
    try {
      const response = await fetch(`${API_BASE}${base}/download?path=${encodeURIComponent(item.path)}`,{headers:{Authorization:`Bearer ${authToken()}`},signal:controller.signal});
      if(!response.ok) throw new Error((await response.json().catch(()=>null))?.error?.message || `下载失败 (${response.status})`);
      const reader = response.body!.getReader(), chunks:Uint8Array<ArrayBuffer>[] = [];
      const total = Number(response.headers.get('Content-Length')); let received = 0;
      while(true) {const result = await reader.read();if(result.done) break;chunks.push(result.value);received += result.value.length;transfer.progress = total ? Math.min(99,Math.round(received/total*100)) : 0;}
      const url = URL.createObjectURL(new Blob(chunks)), anchor = document.createElement('a');
      anchor.href = url; anchor.download = item.name; anchor.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
      transfer.state = 'done'; transfer.progress = 100; transfer.detail = '已交给浏览器保存';
    } catch(reason) {transfer.state = controller.signal.aborted ? 'cancelled' : 'error';transfer.detail = controller.signal.aborted ? '下载已取消' : message(reason);error.value = transfer.detail;}
    finally {abort = undefined;}
    if(transfer.state === 'cancelled') break;
  }
  working.value = false;
}
function drag(event:DragEvent, item:Entry) {
  if(working.value || loading.value) {event.preventDefault();return;}
  if(!selected.value.has(item.path)) select(item);
  event.dataTransfer?.setData('application/x-deebee-files',JSON.stringify({profile:props.profile.id,items:chosen.value}));
  if(event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}
function drop(event:DragEvent, directory=path.value) {
  event.preventDefault(); event.stopPropagation(); dropActive.value = false;
  if(working.value || loading.value) return;
  const raw = event.dataTransfer?.getData('application/x-deebee-files');
  if(raw) {try {const data = JSON.parse(raw);if(data.profile === props.profile.id) void moveTo(directory,data.items);} catch {error.value = '无法读取拖放的文件';} return;}
  if(event.dataTransfer?.files.length) {
    // Browser File objects can represent dropped directories as empty files.
    const items = Array.from(event.dataTransfer.items);
    if(items.some(item=>item.webkitGetAsEntry?.()?.isDirectory)) {error.value = '请上传文件；文件夹请先在远程新建后上传其内容';return;}
    void upload(Array.from(event.dataTransfer.files),directory);
  }
}
async function close() {
  if(working.value && !await confirmAction('有文件操作正在进行。关闭窗口将停止等待结果，远程操作可能已执行。确定关闭？')) return;
  emit('close');
}
onMounted(()=>void navigate(''));
onBeforeUnmount(()=>{disposed = true; navigation++; abort?.();});
</script>

<template>
  <WorkbenchModal visible :title="`文件管理 · ${profile.name}`" :width="1180" :footer="false" :mask-closable="false" :esc-to-close="false" :closable="false" modal-class="sftp-modal" @cancel="close">
    <section class="sftp-manager" aria-label="SSH 文件管理">
      <div class="sftp-connection"><span><Icon icon="lucide:shield-check" /> SFTP <b>{{ profile.user }}@{{ profile.host }}:{{ profile.port }}</b></span><a-button type="text" aria-label="关闭文件管理" @click="close"><Icon icon="lucide:x" /></a-button></div>
      <div class="sftp-toolbar">
        <a-button :disabled="working || loading || !path" @click="uploadInput?.click()"><Icon icon="lucide:upload" /> 上传文件</a-button>
        <a-button :disabled="working || loading || !chosen.length || chosen.some(item=>item.is_dir)" @click="downloadSelected"><Icon icon="lucide:download" /> 下载</a-button>
        <span class="sftp-divider" />
        <a-button :disabled="working || loading || !path" @click="mkdir"><Icon icon="lucide:folder-plus" /> 新建文件夹</a-button>
        <a-button :disabled="working || loading || chosen.length!==1" @click="rename"><Icon icon="lucide:pencil" /> 重命名</a-button>
        <a-button :disabled="working || loading || !chosen.length" @click="moveTo()"><Icon icon="lucide:folder-input" /> 移动</a-button>
        <a-button status="danger" :disabled="working || loading || !chosen.length" @click="remove"><Icon icon="lucide:trash-2" /> 删除</a-button>
        <a-button class="sftp-refresh" :disabled="working || loading" aria-label="刷新文件列表" @click="refresh"><Icon icon="lucide:refresh-cw" /></a-button>
        <input ref="uploadInput" class="sftp-file-input" type="file" multiple aria-label="选择上传文件" @change="pickFiles" />
      </div>
      <form class="sftp-address" @submit.prevent="navigate(address)">
        <a-button :disabled="working || loading || path==='/' || !path" aria-label="上级目录" @click="navigate(parent(path))"><Icon icon="lucide:arrow-up" /></a-button>
        <a-button :disabled="working || loading || !home" aria-label="返回主目录" @click="navigate(home)"><Icon icon="lucide:house" /></a-button>
        <label for="sftp-address">路径</label><input id="sftp-address" v-model="address" aria-label="远程目录路径" placeholder="输入远程绝对路径后按 Enter" :disabled="working" /><a-button html-type="submit" :disabled="working || loading || !address">前往</a-button>
      </form>
      <div v-if="error" class="sftp-error" role="alert"><Icon icon="lucide:circle-alert" /><span>{{ error }}</span></div>
      <div v-if="notice" class="sftp-notice" role="status">{{ notice }}</div>
      <div class="sftp-panes">
        <aside class="sftp-tree" aria-label="远程目录树">
          <div class="sftp-pane-title"><Icon icon="lucide:folders" /> 目录树</div>
          <div role="tree" aria-label="远程文件夹">
            <div v-for="row in tree" :key="`${row.depth}:${row.path}`" class="sftp-tree-row" :class="{active:path===row.path}" :style="{paddingLeft:`${10+row.depth*16}px`}" @dragover.prevent @drop="drop($event,row.path)">
              <button class="sftp-twisty" :disabled="working || treeLoading.has(row.path) || row.link" :aria-label="`${expanded.has(row.path)?'收起':'展开'}目录 ${row.path}`" @click="toggle(row.path)"><Icon :icon="treeLoading.has(row.path)?'lucide:loader-circle':expanded.has(row.path)?'lucide:chevron-down':'lucide:chevron-right'" /></button>
              <button role="treeitem" :aria-label="`打开目录 ${row.path}`" :aria-selected="path===row.path" :aria-expanded="row.link?undefined:expanded.has(row.path)" :disabled="working" :title="row.path" @click="navigate(row.path)"><Icon :icon="row.path===home?'lucide:house':expanded.has(row.path)?'lucide:folder-open':'lucide:folder'" /><span>{{ row.label }}</span><Icon v-if="row.link" icon="lucide:link" /></button>
            </div>
          </div>
        </aside>
        <div class="sftp-files" :class="{'drop-active':dropActive}" :aria-busy="loading" @dragover.prevent="dropActive=true" @dragleave.self="dropActive=false" @drop="drop($event)">
          <div class="sftp-list-controls"><span>{{ loading?'正在读取目录…':`${entries.length} 项` }}</span><input v-model="filter" aria-label="筛选文件" placeholder="筛选文件名…" /><label><input v-model="showHidden" type="checkbox" /> 显示隐藏文件</label></div>
          <div class="sftp-table-scroll">
            <table aria-label="远程文件列表">
              <thead><tr><th class="sftp-check"><input type="checkbox" aria-label="选择全部文件" :checked="allSelected" :disabled="loading || working" @change="checkAll" /></th><th><button @click="changeSort('name')">名称 <span v-if="sort==='name'">{{ ascending?'↑':'↓' }}</span></button></th><th>类型</th><th><button @click="changeSort('size')">大小</button></th><th><button @click="changeSort('modified')">修改时间</button></th><th>权限</th></tr></thead>
              <tbody v-if="!loading">
                <tr v-for="item in visible" :key="item.path" :class="{selected:selected.has(item.path)}" :draggable="!working" tabindex="0" :aria-selected="selected.has(item.path)" @click="select(item,$event)" @keydown.enter.prevent="item.is_dir?navigate(item.path):select(item)" @keydown.space.prevent="check(item)" @dblclick="item.is_dir && navigate(item.path)" @dragstart="drag($event,item)" @dragover.prevent="item.is_dir && $event.stopPropagation()" @drop="item.is_dir && drop($event,item.path)">
                  <td class="sftp-check"><input type="checkbox" :aria-label="`选择 ${item.name}`" :checked="selected.has(item.path)" :disabled="working" @click.stop @change="check(item)" /></td><td><span class="sftp-filename" :title="item.name"><Icon :icon="item.is_dir?'lucide:folder':item.is_link?'lucide:file-symlink':'lucide:file'" :class="{folder:item.is_dir}" /><span>{{ item.name }}</span><Icon v-if="item.is_link" icon="lucide:link" /></span></td><td>{{ item.is_link?'符号链接':item.is_dir?'文件夹':'文件' }}</td><td>{{ item.is_dir?'—':size(item.size) }}</td><td>{{ date(item.modified) }}</td><td class="sftp-permissions">{{ item.permissions }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="loading" class="sftp-empty"><Icon icon="lucide:loader-circle" /> 正在连接并读取文件…</div>
            <div v-else-if="!visible.length" class="sftp-empty"><Icon icon="lucide:folder-open" /><strong>{{ !path?'尚未读取目录':filter?'没有匹配的文件':'此目录为空' }}</strong><span>{{ !path?'检查连接后点击刷新重试':'可点击上传文件，或将本地文件拖到此处' }}</span></div>
          </div>
        </div>
      </div>
      <div v-if="transfers.length" class="sftp-transfers" aria-label="文件传输记录"><div class="sftp-transfer-title"><b>传输记录</b><span>单文件最大 2 GB · 同名文件不会覆盖</span><a-button v-if="working && abort" type="text" @click="abort?.()">取消传输</a-button><a-button v-if="!working" type="text" @click="transfers=[]">清空记录</a-button></div><div class="sftp-transfer-list"><div v-for="item in transfers" :key="item.id" class="sftp-transfer" :class="item.state"><span>{{ item.direction }}</span><b :title="item.name">{{ item.name }}</b><progress :value="item.progress" max="100" /><span>{{ item.state==='running'?`${item.progress}%`:item.state==='done'?'完成':item.state==='cancelled'?'已取消':'失败' }}</span><small>{{ item.detail }}</small></div></div></div>
      <footer class="sftp-status"><span><Icon icon="lucide:server" /> {{ profile.name }}</span><span>{{ selected.size?`已选择 ${selected.size} 项`:'双击文件夹进入 · 支持拖放上传和拖动移动' }}</span><span>{{ working?'正在执行文件操作…':'SFTP' }}</span></footer>
    </section>
  </WorkbenchModal>
</template>

<style>
.sftp-modal{max-width:calc(100vw - 40px)}.sftp-modal .arco-modal-body{padding:0!important}.sftp-modal .arco-modal-header{padding:16px 20px;margin:0;border-bottom:1px solid var(--color-border-2)}
</style>
<style scoped>
.sftp-manager{display:flex;flex-direction:column;max-height:calc(100dvh - 170px);overflow:hidden}
.sftp-manager>.sftp-panes{flex:1 1 auto;min-height:160px}
.sftp-manager>.sftp-connection,.sftp-manager>.sftp-toolbar,.sftp-manager>.sftp-address,.sftp-manager>.sftp-status,.sftp-manager>.sftp-transfers,.sftp-manager>.sftp-error,.sftp-manager>.sftp-notice{flex-shrink:0}
.sftp-manager{color:var(--color-text-1);font-size:12px}.sftp-manager svg{width:16px;height:16px;flex-shrink:0}.sftp-connection,.sftp-toolbar,.sftp-address,.sftp-status,.sftp-list-controls,.sftp-transfer-title{display:flex;align-items:center;gap:8px}.sftp-connection{padding:4px 14px;background:var(--color-fill-1);justify-content:space-between}.sftp-connection>span{display:flex;align-items:center;gap:8px;color:var(--color-text-3)}.sftp-connection>span>svg{color:rgb(var(--success-6))}.sftp-connection b{font-weight:500;color:var(--color-text-2)}.sftp-toolbar{padding:10px 14px;border-bottom:1px solid var(--color-border-2);flex-wrap:wrap}.sftp-toolbar .arco-btn{gap:6px;font-size:12px}.sftp-divider{height:20px;border-left:1px solid var(--color-border-2);margin:0 2px}.sftp-refresh{margin-left:auto}.sftp-file-input{display:none}.sftp-address{padding:10px 14px;border-bottom:1px solid var(--color-border-2)}.sftp-address>input,.sftp-list-controls>input{background:var(--color-bg-2);border:1px solid var(--color-border-3);border-radius:4px;padding:6px 9px;color:var(--color-text-1);outline:none}.sftp-address>input:focus,.sftp-list-controls>input:focus{border-color:rgb(var(--primary-6))}.sftp-address>input{flex:1;min-width:0;font:12px ui-monospace,monospace}.sftp-error,.sftp-notice{padding:9px 16px;display:flex;align-items:center;gap:8px;overflow-wrap:anywhere}.sftp-error{color:rgb(var(--danger-6));background:var(--color-danger-light-1)}.sftp-notice{color:rgb(var(--success-6));background:var(--color-success-light-1)}.sftp-panes{display:flex;height:clamp(220px,48vh,540px);min-height:0}.sftp-tree{width:240px;flex:none;border-right:1px solid var(--color-border-2);overflow:auto;background:var(--color-fill-1)}.sftp-pane-title{display:flex;align-items:center;gap:8px;padding:12px;color:var(--color-text-3);font-weight:600}.sftp-tree-row{display:flex;align-items:center;min-height:32px;padding-right:8px}.sftp-tree-row.active{background:var(--color-primary-light-1)}.sftp-tree-row:hover{background:var(--color-fill-2)}.sftp-tree-row button{border:0;background:none;display:flex;align-items:center;gap:7px;min-width:0;cursor:pointer;color:var(--color-text-2);padding:6px 0;font:inherit}.sftp-tree-row button[role=treeitem]{flex:1;text-align:left}.sftp-tree-row button span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sftp-tree-row .sftp-twisty{width:22px;flex:none}.sftp-tree-row .sftp-twisty:disabled{opacity:.4}.sftp-tree-row.active button{color:rgb(var(--primary-6))}.sftp-files{display:flex;flex-direction:column;flex:1;min-width:0;position:relative}.sftp-files.drop-active{outline:2px dashed rgb(var(--primary-6));outline-offset:-3px;background:var(--color-primary-light-1)}.sftp-list-controls{padding:8px 12px;min-height:42px;border-bottom:1px solid var(--color-border-2)}.sftp-list-controls>span{color:var(--color-text-3);margin-right:auto}.sftp-list-controls>input{width:160px}.sftp-list-controls label{display:flex;align-items:center;gap:4px;white-space:nowrap;color:var(--color-text-2)}.sftp-table-scroll{overflow:auto;flex:1}.sftp-files table{width:100%;border-collapse:collapse;white-space:nowrap;text-align:left}.sftp-files thead{position:sticky;top:0;background:var(--color-fill-2);z-index:1}.sftp-files th{font-weight:500;color:var(--color-text-2);height:34px}.sftp-files td,.sftp-files th{padding:0 12px;border-bottom:1px solid var(--color-border-1)}.sftp-files th button{border:0;background:none;color:inherit;font:inherit;cursor:pointer;padding:0}.sftp-files td{height:36px;color:var(--color-text-2)}.sftp-files td:nth-child(2){color:var(--color-text-1)}.sftp-files .sftp-check{padding:0 8px 0 12px;width:28px}.sftp-files tbody tr{cursor:default}.sftp-files tbody tr:hover{background:var(--color-fill-1)}.sftp-files tbody tr.selected{background:var(--color-primary-light-1)}.sftp-filename{display:flex;align-items:center;gap:8px;max-width:330px}.sftp-filename>span{max-width:270px;overflow:hidden;text-overflow:ellipsis}.sftp-filename .folder{color:#d99b32}.sftp-permissions{font-family:ui-monospace,monospace;font-size:11px}.sftp-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;min-height:200px;color:var(--color-text-3)}.sftp-empty>svg{width:32px;height:32px}.sftp-empty strong{font-weight:500;color:var(--color-text-2)}.sftp-transfers{border-top:1px solid var(--color-border-2)}.sftp-transfer-title{padding:5px 14px;background:var(--color-fill-1)}.sftp-transfer-title>span{font-size:11px;color:var(--color-text-3);margin-right:auto}.sftp-transfer-list{max-height:108px;overflow:auto}.sftp-transfer{display:flex;align-items:center;gap:12px;padding:7px 14px;border-top:1px solid var(--color-border-1)}.sftp-transfer>b{font-weight:500;min-width:80px;width:22%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sftp-transfer progress{width:110px;height:6px;accent-color:rgb(var(--primary-6))}.sftp-transfer small{color:var(--color-text-3);overflow-wrap:anywhere}.sftp-transfer.error>span,.sftp-transfer.error small{color:rgb(var(--danger-6))}.sftp-status{border-top:1px solid var(--color-border-2);background:var(--color-fill-1);padding:8px 14px;color:var(--color-text-3);justify-content:space-between;font-size:11px}.sftp-status>span{display:flex;align-items:center;gap:6px}@media(max-width:800px){.sftp-tree{width:180px}.sftp-list-controls{flex-wrap:wrap}.sftp-list-controls>input{width:120px}.sftp-status>span:nth-child(2){display:none}.sftp-transfer-title>span{display:none}}
</style>
