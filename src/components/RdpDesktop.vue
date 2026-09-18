<script setup lang="ts">
import { Icon } from "@iconify/vue";
import Guacamole from "guacamole-common-js";
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { API_BASE, authToken } from "../api";

const props = defineProps<{ profileId: string; title: string; active: boolean }>();
const emit = defineEmits<{ state: [value: "online" | "checking" | "offline"] }>();

const viewport = ref<HTMLElement>();
const state = ref<"connecting" | "connected" | "closed" | "error">("connecting");
const message = ref("正在建立 RDP 会话");
let client: Guacamole.Client | undefined;
let mouse: Guacamole.Mouse | undefined;
let keyboard: Guacamole.Keyboard | undefined;
let resizeObserver: ResizeObserver | undefined;
let reconnectTimer: number | undefined;
let resizeFrame: number | undefined;
let connectionGeneration = 0;
let reconnectAttempts = 0;
let disposed = false;
let lastVisibleSize = { width: 1280, height: 720 };

function tunnelUrl() {
  const url = new URL(`${API_BASE}/connections/${encodeURIComponent(props.profileId)}/rdp`, window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function visibleRect() {
  if (!props.active || !viewport.value) return;
  const rect = viewport.value.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  return rect;
}

function dimensions() {
  const rect = visibleRect();
  if (!rect) return;
  lastVisibleSize = { width: Math.max(320, Math.floor(rect.width)), height: Math.max(240, Math.floor(rect.height)) };
  return lastVisibleSize;
}

function scaleDisplay() {
  const rect = visibleRect();
  if (!client || !rect) return;
  const display = client.getDisplay();
  const remoteWidth = display.getWidth();
  const remoteHeight = display.getHeight();
  if (remoteWidth <= 1 || remoteHeight <= 1) return;
  display.scale(Math.min(rect.width / remoteWidth, rect.height / remoteHeight));
}

function resize() {
  const size = dimensions();
  if (!client || !size || state.value !== "connected") return;
  const { width, height } = size;
  client.sendSize(width, height);
  scaleDisplay();
}

function clearReconnect() {
  if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
  reconnectTimer = undefined;
}

function clearResizeFrame() {
  if (resizeFrame !== undefined) window.cancelAnimationFrame(resizeFrame);
  resizeFrame = undefined;
}

function disconnect() {
  connectionGeneration += 1;
  clearReconnect();
  clearResizeFrame();
  keyboard?.reset();
  if (keyboard) {
    keyboard.onkeydown = null;
    keyboard.onkeyup = null;
  }
  mouse?.reset();
  if (client) {
    client.onstatechange = null;
    client.onerror = null;
    client.getDisplay().onresize = null;
    client.disconnect();
  }
  client = undefined;
  mouse = undefined;
  keyboard = undefined;
  if (viewport.value) viewport.value.replaceChildren();
}

function scheduleReconnect() {
  if (disposed || !props.active || reconnectTimer !== undefined || reconnectAttempts >= 3) return;
  reconnectAttempts += 1;
  const delay = Math.min(5000, 1000 * 2 ** (reconnectAttempts - 1));
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined;
    if (props.active && !disposed) connect();
  }, delay);
}

function refreshViewport(focus = false) {
  clearResizeFrame();
  void nextTick(() => {
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = undefined;
        resize();
        if (focus) viewport.value?.focus();
      });
    });
  });
}

function connect() {
  disconnect();
  if (!viewport.value || !props.active || disposed) return;
  const generation = connectionGeneration;
  state.value = "connecting";
  message.value = "正在建立 RDP 会话";
  emit("state", "checking");
  const tunnel = new Guacamole.WebSocketTunnel(tunnelUrl());
  // Background tabs may throttle timers. The backend also sends regular
  // Guacamole keepalives, while this wider window avoids false disconnects.
  tunnel.receiveTimeout = 60_000;
  tunnel.unstableThreshold = 10_000;
  const sessionClient = new Guacamole.Client(tunnel);
  client = sessionClient;
  const display = sessionClient.getDisplay();
  display.onresize = scaleDisplay;
  const displayElement = display.getElement();
  displayElement.classList.add("guacamole-display");
  viewport.value.appendChild(displayElement);

  mouse = new Guacamole.Mouse(displayElement);
  mouse.onEach(["mousemove", "mousedown", "mouseup"], (_event, target) => {
    if (generation === connectionGeneration) sessionClient.sendMouseState((target as Guacamole.Mouse).currentState, true);
  });
  keyboard = new Guacamole.Keyboard(viewport.value);
  keyboard.onkeydown = keysym => { if (generation === connectionGeneration) sessionClient.sendKeyEvent(1, keysym); return false; };
  keyboard.onkeyup = keysym => { if (generation === connectionGeneration) sessionClient.sendKeyEvent(0, keysym); };

  sessionClient.onstatechange = next => {
    if (generation !== connectionGeneration || sessionClient !== client) return;
    if (next === Guacamole.Client.State.CONNECTED) {
      reconnectAttempts = 0;
      state.value = "connected";
      message.value = "远程桌面已连接";
      emit("state", "online");
      refreshViewport(true);
    }
    else if (next === Guacamole.Client.State.DISCONNECTED) {
      state.value = "closed";
      message.value = "远程桌面已断开";
      emit("state", "offline");
      scheduleReconnect();
    }
  };
  sessionClient.onerror = status => {
    if (generation !== connectionGeneration || sessionClient !== client) return;
    state.value = "error";
    message.value = status.message || "RDP 连接失败";
    emit("state", "offline");
    scheduleReconnect();
  };
  const { width, height } = dimensions() ?? lastVisibleSize;
  sessionClient.connect(new URLSearchParams({ token: authToken(), width: String(width), height: String(height), dpi: "96" }).toString());
}

async function toggleFullscreen() {
  if (!viewport.value) return;
  if (document.fullscreenElement) await document.exitFullscreen(); else await viewport.value.requestFullscreen();
  window.setTimeout(resize, 80);
}

onMounted(async () => {
  await nextTick();
  resizeObserver = new ResizeObserver(() => { if (props.active) resize(); });
  if (viewport.value) resizeObserver.observe(viewport.value);
  if (props.active) connect();
});
watch(() => props.active, active => {
  if (!active) {
    clearReconnect();
    clearResizeFrame();
    keyboard?.reset();
    return;
  }
  if (!client || state.value === "closed" || state.value === "error") {
    void nextTick(() => window.requestAnimationFrame(connect));
    return;
  }
  refreshViewport(true);
});
onBeforeUnmount(() => { disposed = true; resizeObserver?.disconnect(); disconnect(); });
</script>

<template>
  <section class="view remote-session rdp-session">
    <header class="remote-toolbar">
      <div><i :class="`remote-dot ${state}`" /><strong>{{ title }}</strong><span>{{ message }}</span></div>
      <nav>
        <button @click="toggleFullscreen"><Icon icon="lucide:maximize-2" />全屏</button>
        <button @click="connect"><Icon icon="lucide:refresh-cw" />重新连接</button>
      </nav>
    </header>
    <div ref="viewport" class="rdp-viewport" tabindex="0" />
  </section>
</template>
