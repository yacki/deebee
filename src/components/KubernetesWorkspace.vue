<script setup lang="ts">
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Icon } from "@iconify/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { API_BASE, api, authToken } from "../api";

const props = defineProps<{ profileId: string; title: string; active: boolean }>();
const emit = defineEmits<{ state: [value: "online" | "checking" | "offline"] }>();

type Container = { name: string; type: "container" };
type Pod = { name: string; type: "pod"; containers: Container[] };
type Namespace = { name: string; type: "namespace"; pods: Pod[] };
type Target = { namespace: string; pod: string; container: string; label: string };
type TerminalTab = Target & { id: string; status: "opening" | "connected" | "closed" | "error"; message: string };

const tree = ref<Namespace[]>([]);
const loadingTree = ref(false);
const treeError = ref("");
const search = ref("");
const searchOpen = ref(false);
const expandedNamespaces = ref(new Set<string>());
const expandedPods = ref(new Set<string>());
const tabs = ref<TerminalTab[]>([]);
const activeTabId = ref("");
const state = ref<"connecting" | "connected" | "closed" | "error">("connecting");
const message = ref("正在建立 Kubernetes 会话");
const sidebarWidth = ref(276);
const recent = ref<Target[]>([]);
const terminals = new Map<string, { terminal: Terminal; fit: FitAddon; observer: ResizeObserver; disposable: { dispose: () => void } }>();
let socket: WebSocket | undefined;
let reconnectTimer: number | undefined;
let reconnectAttempts = 0;
let disposed = false;
let resizing = false;
let resizeStartX = 0;
let resizeStartWidth = 0;

const activeTab = computed(() => tabs.value.find(item => item.id === activeTabId.value));
const filteredContainers = computed(() => {
  const query = search.value.trim().toLowerCase();
  if (!query) return [];
  const result: Target[] = [];
  for (const namespace of tree.value) for (const pod of namespace.pods) for (const container of pod.containers) {
    const label = `${namespace.name}/${pod.name}/${container.name}`;
    if (label.toLowerCase().includes(query)) result.push({ namespace: namespace.name, pod: pod.name, container: container.name, label });
  }
  return result;
});

function recentKey() { return `deebee_k8s_recent:${props.profileId}`; }
function loadRecent() {
  try { recent.value = JSON.parse(localStorage.getItem(recentKey()) || "[]").slice(0, 10); }
  catch { recent.value = []; }
}
function remember(target: Target) {
  if (!target.container) return;
  recent.value = [target, ...recent.value.filter(item => item.label !== target.label)].slice(0, 10);
  localStorage.setItem(recentKey(), JSON.stringify(recent.value));
}
function clearRecent() { recent.value = []; localStorage.removeItem(recentKey()); }
function podKey(namespace: string, pod: string) { return `${namespace}/${pod}`; }
function toggleNamespace(namespace: Namespace) {
  const next = new Set(expandedNamespaces.value);
  if (next.has(namespace.name)) next.delete(namespace.name); else {
    next.add(namespace.name);
    for (const pod of namespace.pods) if (pod.containers.length === 1) expandedPods.value.add(podKey(namespace.name, pod.name));
  }
  expandedNamespaces.value = next;
}
function togglePod(namespace: string, pod: Pod) {
  const key = podKey(namespace, pod.name); const next = new Set(expandedPods.value);
  if (next.has(key)) next.delete(key); else next.add(key);
  expandedPods.value = next;
}

function socketUrl() {
  const url = new URL(`${API_BASE}/connections/${encodeURIComponent(props.profileId)}/k8s`, window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
function send(payload: object) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload)); }
function connect() {
  socket?.close();
  if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
  state.value = "connecting"; message.value = "正在建立 Kubernetes 会话"; emit("state", "checking");
  const target = new WebSocket(socketUrl()); socket = target;
  target.onopen = () => { if (socket !== target) return; reconnectAttempts = 0; send({ type: "auth", token: authToken() }); };
  target.onmessage = event => {
    if (socket !== target) return;
    const payload = JSON.parse(String(event.data)) as { type: string; state?: string; id?: string; data?: string; message?: string; exit_code?: number };
    if (payload.type === "state") {
      state.value = payload.state === "connected" ? "connected" : "error";
      message.value = payload.message || (state.value === "connected" ? "Kubernetes 已连接" : "Kubernetes 连接失败");
      emit("state", state.value === "connected" ? "online" : "offline");
      if (state.value === "connected") void refreshTree();
      return;
    }
    const item = tabs.value.find(tab => tab.id === payload.id);
    if (!item) return;
    if (payload.type === "data") terminals.get(item.id)?.terminal.write(payload.data || "");
    else if (payload.type === "opened") { item.status = "connected"; item.message = "已连接"; terminals.get(item.id)?.terminal.focus(); }
    else if (payload.type === "closed") { item.status = "closed"; item.message = `会话已结束${payload.exit_code ? `（${payload.exit_code}）` : ""}`; }
    else if (payload.type === "error") { item.status = "error"; item.message = payload.message || "终端连接失败"; terminals.get(item.id)?.terminal.writeln(`\r\n\x1b[31m${item.message}\x1b[0m`); }
  };
  target.onerror = () => { if (socket !== target) return; state.value = "error"; message.value = "Kubernetes 实时通道连接失败"; emit("state", "offline"); };
  target.onclose = () => {
    if (disposed || socket !== target) return;
    state.value = "closed"; message.value = "Kubernetes 会话已断开"; emit("state", "offline");
    if (props.active && reconnectAttempts < 3) {
      reconnectAttempts += 1;
      reconnectTimer = window.setTimeout(connect, Math.min(5000, 800 * 2 ** reconnectAttempts));
    }
  };
}

async function refreshTree() {
  loadingTree.value = true; treeError.value = "";
  try {
    tree.value = await api<Namespace[]>(`/connections/${encodeURIComponent(props.profileId)}/k8s/tree`);
    if (!expandedNamespaces.value.size && tree.value.length === 1) expandedNamespaces.value = new Set([tree.value[0].name]);
  } catch (reason) { treeError.value = reason instanceof Error ? reason.message : "资源树加载失败"; }
  finally { loadingTree.value = false; }
}

function terminalElementId(id: string) { return `k8s-terminal-${props.profileId}-${id}`; }
function fitTerminal(id: string) {
  const target = terminals.get(id); const element = document.getElementById(terminalElementId(id));
  if (!target || !element || element.clientWidth < 2 || element.clientHeight < 2) return;
  target.fit.fit(); send({ type: "resize", id, cols: target.terminal.cols, rows: target.terminal.rows });
}
function mountTerminal(item: TerminalTab) {
  const element = document.getElementById(terminalElementId(item.id));
  if (!element || terminals.has(item.id)) return;
  const terminal = new Terminal({ cursorBlink: true, cursorStyle: "bar", fontFamily: "SFMono-Regular, Menlo, Monaco, Consolas, monospace", fontSize: 14, lineHeight: 1.16, scrollback: 10000, theme: { background: "#0b1220", foreground: "#d9e2f2", cursor: "#48a6ff", selectionBackground: "#24588a88" } });
  const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(element); fit.fit();
  terminal.writeln(`\x1b[38;5;75m正在连接 ${item.label}…\x1b[0m`);
  const disposable = terminal.onData(data => send({ type: "data", id: item.id, data }));
  const observer = new ResizeObserver(() => fitTerminal(item.id)); observer.observe(element);
  terminals.set(item.id, { terminal, fit, observer, disposable });
  send({ type: "open", id: item.id, namespace: item.namespace, pod: item.pod, container: item.container, cols: terminal.cols, rows: terminal.rows });
}
function openTerminal(target: Target) {
  if (state.value !== "connected") return;
  remember(target);
  const item: TerminalTab = { ...target, id: crypto.randomUUID(), status: "opening", message: "正在连接" };
  tabs.value.push(item); activeTabId.value = item.id;
  void nextTick(() => mountTerminal(item));
}
function openCluster() { openTerminal({ namespace: "", pod: "", container: "", label: props.title.replace(/\s+\d+$/, "") }); }
function openContainer(namespace: string, pod: string, container: string) { openTerminal({ namespace, pod, container, label: `${namespace}/${pod}/${container}` }); }
function closeTab(item: TerminalTab) {
  send({ type: "close", id: item.id });
  const terminal = terminals.get(item.id); terminal?.observer.disconnect(); terminal?.disposable.dispose(); terminal?.terminal.dispose(); terminals.delete(item.id);
  const index = tabs.value.indexOf(item); tabs.value.splice(index, 1);
  if (activeTabId.value === item.id) activeTabId.value = tabs.value[Math.max(0, index - 1)]?.id || "";
}
async function copySelection() { const value = activeTab.value && terminals.get(activeTab.value.id)?.terminal.getSelection(); if (value) await navigator.clipboard.writeText(value); }
async function pasteClipboard() { const value = await navigator.clipboard.readText(); const item = activeTab.value; if (value && item) send({ type: "data", id: item.id, data: value }); }

function startResize(event: PointerEvent) { resizing = true; resizeStartX = event.clientX; resizeStartWidth = sidebarWidth.value; (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); }
function moveResize(event: PointerEvent) { if (!resizing) return; sidebarWidth.value = Math.max(220, Math.min(420, resizeStartWidth + event.clientX - resizeStartX)); if (activeTabId.value) fitTerminal(activeTabId.value); }
function stopResize() { resizing = false; }

onMounted(() => { loadRecent(); connect(); window.addEventListener("pointermove", moveResize); window.addEventListener("pointerup", stopResize); });
watch(() => props.active, active => { if (active) nextTick(() => { if (!socket || socket.readyState > WebSocket.OPEN) connect(); if (activeTabId.value) { fitTerminal(activeTabId.value); terminals.get(activeTabId.value)?.terminal.focus(); } }); });
watch(activeTabId, id => { if (id) nextTick(() => { fitTerminal(id); terminals.get(id)?.terminal.focus(); }); });
onBeforeUnmount(() => {
  disposed = true; if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
  for (const item of tabs.value) send({ type: "close", id: item.id });
  socket?.close(); window.removeEventListener("pointermove", moveResize); window.removeEventListener("pointerup", stopResize);
  for (const value of terminals.values()) { value.observer.disconnect(); value.disposable.dispose(); value.terminal.dispose(); }
  terminals.clear();
});
</script>

<template>
  <section class="view k8s-workspace">
    <aside class="k8s-tree" :style="{ width: `${sidebarWidth}px` }">
      <header><strong>Kubernetes</strong><nav><button title="集群终端" :disabled="state!=='connected'" @click="openCluster"><Icon icon="lucide:square-terminal" /></button><button title="搜索容器" :class="{active:searchOpen}" @click="searchOpen=!searchOpen"><Icon icon="lucide:search" /></button><button title="刷新资源树" :disabled="loadingTree" @click="refreshTree"><Icon icon="lucide:refresh-cw" :class="{spin:loadingTree}" /></button></nav></header>
      <label v-if="searchOpen" class="k8s-search"><Icon icon="lucide:search" /><input v-model="search" autofocus placeholder="筛选容器" /></label>
      <div class="k8s-tree-body">
        <template v-if="search.trim()">
          <button v-for="item in filteredContainers" :key="item.label" class="k8s-row container-row search-result" :title="item.label" @click="openTerminal(item)"><span class="k8s-spacer" /><Icon icon="lucide:container" /><span>{{ item.label }}</span></button>
          <p v-if="!filteredContainers.length" class="k8s-tree-note">没有匹配的容器</p>
        </template>
        <template v-else>
          <div class="k8s-root-row"><button class="k8s-row"><Icon icon="lucide:chevron-down" /><Icon icon="lucide:history" /><span>最近容器</span></button><button v-if="recent.length" title="清空最近容器" @click="clearRecent"><Icon icon="lucide:trash-2" /></button></div>
          <button v-for="item in recent" :key="`recent-${item.label}`" class="k8s-row container-row depth-one" :title="item.label" @click="openTerminal(item)"><span class="k8s-spacer" /><Icon icon="lucide:container" /><span>{{ item.label }}</span></button>
          <div class="k8s-root-row"><button class="k8s-row"><Icon icon="lucide:chevron-down" /><Icon icon="lucide:ship-wheel" /><span>{{ title }}</span></button></div>
          <p v-if="treeError" class="k8s-tree-note error">{{ treeError }}</p><p v-else-if="loadingTree" class="k8s-tree-note">正在加载 Pod…</p>
          <template v-for="namespace in tree" :key="namespace.name">
            <button class="k8s-row depth-one" @click="toggleNamespace(namespace)"><Icon icon="lucide:chevron-right" :class="{expanded:expandedNamespaces.has(namespace.name)}" /><Icon :icon="expandedNamespaces.has(namespace.name)?'lucide:folder-open':'lucide:folder'" /><span>{{ namespace.name }}</span><b>{{ namespace.pods.length }}</b></button>
            <template v-if="expandedNamespaces.has(namespace.name)">
              <template v-for="pod in namespace.pods" :key="pod.name">
                <button class="k8s-row depth-two" @click="togglePod(namespace.name,pod)"><Icon icon="lucide:chevron-right" :class="{expanded:expandedPods.has(podKey(namespace.name,pod.name))}" /><Icon icon="lucide:box" /><span>{{ pod.name }}</span><b>{{ pod.containers.length }}</b></button>
                <button v-for="container in expandedPods.has(podKey(namespace.name,pod.name))?pod.containers:[]" :key="container.name" class="k8s-row container-row depth-three" :title="`${namespace.name}/${pod.name}/${container.name}`" @click="openContainer(namespace.name,pod.name,container.name)"><span class="k8s-spacer" /><Icon icon="lucide:container" /><span>{{ container.name }}</span></button>
              </template>
            </template>
          </template>
        </template>
      </div>
    </aside>
    <div class="k8s-resizer" role="separator" aria-label="调整 Kubernetes 资源树宽度" @pointerdown="startResize" />
    <div class="k8s-terminal-area">
      <header class="remote-toolbar k8s-toolbar"><div><i :class="`remote-dot ${state}`" /><strong>{{ title }}</strong><span>{{ message }}</span></div><nav><button :disabled="!activeTab" @click="copySelection"><Icon icon="lucide:copy" />复制</button><button :disabled="!activeTab" @click="pasteClipboard"><Icon icon="lucide:clipboard-paste" />粘贴</button><button :disabled="!activeTab" @click="activeTab&&terminals.get(activeTab.id)?.terminal.clear()"><Icon icon="lucide:eraser" />清屏</button><button @click="connect"><Icon icon="lucide:refresh-cw" />重新连接</button></nav></header>
      <nav class="k8s-subtabs"><button v-for="item in tabs" :key="item.id" :class="{active:activeTabId===item.id}" :title="item.label" @click="activeTabId=item.id"><Icon :icon="item.container?'lucide:container':'lucide:boxes'" /><span>{{ item.label }}</span><i :class="item.status" /><b @click.stop="closeTab(item)"><Icon icon="lucide:x" /></b></button></nav>
      <div class="k8s-terminal-stack">
        <div v-for="item in tabs" v-show="activeTabId===item.id" :id="terminalElementId(item.id)" :key="item.id" class="terminal-host k8s-terminal" />
        <div v-if="!tabs.length" class="k8s-empty"><Icon icon="lucide:square-terminal" /><strong>选择容器或打开集群终端</strong><span>可同时打开多个容器会话</span><button class="primary" :disabled="state!=='connected'" @click="openCluster"><Icon icon="lucide:square-terminal" />连接集群</button></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.k8s-workspace { flex-direction: row; }
</style>
