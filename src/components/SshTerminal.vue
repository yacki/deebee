<script setup lang="ts">
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Icon } from "@iconify/vue";
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { API_BASE, authToken } from "../api";

const props = defineProps<{ profileId: string; title: string; active: boolean }>();
const emit = defineEmits<{ state: [value: "online" | "checking" | "offline"] }>();
type SessionState = "connecting" | "connected" | "closed" | "error";

const host = ref<HTMLElement>();
const state = ref<SessionState>("connecting");
const message = ref("");
let terminal: Terminal | undefined;
let fit: FitAddon | undefined;
let socket: WebSocket | undefined;
let resizeObserver: ResizeObserver | undefined;
let dataDisposable: { dispose: () => void } | undefined;

function socketUrl() {
  const url = new URL(`${API_BASE}/connections/${encodeURIComponent(props.profileId)}/ssh`, window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", authToken());
  url.searchParams.set("cols", String(terminal?.cols || 120));
  url.searchParams.set("rows", String(terminal?.rows || 32));
  return url.toString();
}

function send(value: object) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value));
}

function resize() {
  if (!fit || !terminal || !host.value || host.value.clientWidth === 0) return;
  fit.fit();
  send({ type: "resize", cols: terminal.cols, rows: terminal.rows });
}

function connect() {
  socket?.close();
  state.value = "connecting";
  message.value = "正在建立 SSH 会话";
  emit("state", "checking");
  terminal?.reset();
  terminal?.writeln("\x1b[38;5;75m正在连接 " + props.title + "…\x1b[0m");
  socket = new WebSocket(socketUrl());
  socket.onmessage = event => {
    const payload = JSON.parse(String(event.data)) as { type: string; state?: SessionState; data?: string; message?: string };
    if (payload.type === "data") terminal?.write(payload.data || "");
    if (payload.type === "state" && payload.state) {
      const nextState: SessionState = payload.state;
      state.value = nextState;
      message.value = payload.message || "";
      emit("state", nextState === "connected" ? "online" : nextState === "connecting" ? "checking" : "offline");
      if (nextState === "error") terminal?.writeln(`\r\n\x1b[31m${payload.message || "SSH 连接失败"}\x1b[0m`);
      if (nextState === "connected") terminal?.focus();
    }
  };
  socket.onerror = () => {
    state.value = "error";
    message.value = "SSH 实时通道连接失败";
    emit("state", "offline");
  };
  socket.onclose = () => {
    if (state.value === "connected" || state.value === "connecting") {
      state.value = "closed";
      message.value = "会话已断开";
      emit("state", "offline");
    }
  };
}

async function copySelection() {
  const selected = terminal?.getSelection();
  if (selected) await navigator.clipboard.writeText(selected);
}

async function pasteClipboard() {
  const value = await navigator.clipboard.readText();
  if (value) terminal?.paste(value);
}

onMounted(async () => {
  await nextTick();
  if (!host.value) return;
  terminal = new Terminal({
    cursorBlink: true,
    cursorStyle: "bar",
    fontFamily: "SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    fontSize: 14,
    lineHeight: 1.16,
    scrollback: 10000,
    allowProposedApi: false,
    theme: { background: "#0b1220", foreground: "#d9e2f2", cursor: "#48a6ff", selectionBackground: "#24588a88" },
  });
  fit = new FitAddon();
  terminal.loadAddon(fit);
  terminal.open(host.value);
  resize();
  dataDisposable = terminal.onData(data => send({ type: "data", data }));
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host.value);
  connect();
});

watch(() => props.active, active => { if (active) nextTick(() => { resize(); terminal?.focus(); }); });
onBeforeUnmount(() => {
  send({ type: "close" });
  socket?.close();
  resizeObserver?.disconnect();
  dataDisposable?.dispose();
  terminal?.dispose();
});
</script>

<template>
  <section class="view remote-session ssh-session">
    <header class="remote-toolbar">
      <div><i :class="`remote-dot ${state}`" /><strong>{{ title }}</strong><span>{{ message }}</span></div>
      <nav>
        <button @click="copySelection"><Icon icon="lucide:copy" />复制</button>
        <button @click="pasteClipboard"><Icon icon="lucide:clipboard-paste" />粘贴</button>
        <button @click="terminal?.clear()"><Icon icon="lucide:eraser" />清屏</button>
        <button @click="connect"><Icon icon="lucide:refresh-cw" />重新连接</button>
      </nav>
    </header>
    <div ref="host" class="terminal-host" @click="terminal?.focus()" />
  </section>
</template>
