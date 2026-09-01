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

function tunnelUrl() {
  const url = new URL(`${API_BASE}/connections/${encodeURIComponent(props.profileId)}/rdp`, window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function dimensions() {
  const rect = viewport.value?.getBoundingClientRect();
  return { width: Math.max(320, Math.floor(rect?.width || 1280)), height: Math.max(240, Math.floor(rect?.height || 720)) };
}

function scaleDisplay() {
  if (!client || !viewport.value) return;
  const display = client.getDisplay();
  const remoteWidth = display.getWidth();
  const remoteHeight = display.getHeight();
  if (remoteWidth <= 1 || remoteHeight <= 1) return;
  const rect = viewport.value.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  display.scale(Math.min(width / remoteWidth, height / remoteHeight));
}

function resize() {
  if (!client || !viewport.value) return;
  const { width, height } = dimensions();
  client.sendSize(width, height);
  scaleDisplay();
}

function disconnect() {
  keyboard?.reset();
  client?.disconnect();
  client = undefined;
  mouse = undefined;
  keyboard = undefined;
  if (viewport.value) viewport.value.replaceChildren();
}

function connect() {
  disconnect();
  if (!viewport.value) return;
  state.value = "connecting";
  message.value = "正在建立 RDP 会话";
  emit("state", "checking");
  const tunnel = new Guacamole.WebSocketTunnel(tunnelUrl());
  client = new Guacamole.Client(tunnel);
  const display = client.getDisplay();
  display.onresize = scaleDisplay;
  const displayElement = display.getElement();
  displayElement.classList.add("guacamole-display");
  viewport.value.appendChild(displayElement);

  mouse = new Guacamole.Mouse(displayElement);
  mouse.onEach(["mousemove", "mousedown", "mouseup"], (_event, target) => {
    client?.sendMouseState((target as Guacamole.Mouse).currentState, true);
  });
  keyboard = new Guacamole.Keyboard(viewport.value);
  keyboard.onkeydown = keysym => { client?.sendKeyEvent(1, keysym); return false; };
  keyboard.onkeyup = keysym => client?.sendKeyEvent(0, keysym);

  client.onstatechange = next => {
    if (next === Guacamole.Client.State.CONNECTED) {
      state.value = "connected";
      message.value = "远程桌面已连接";
      emit("state", "online");
      resize();
      viewport.value?.focus();
    }
    else if (next === Guacamole.Client.State.DISCONNECTED) {
      state.value = "closed";
      message.value = "远程桌面已断开";
      emit("state", "offline");
    }
  };
  client.onerror = status => {
    state.value = "error";
    message.value = status.message || "RDP 连接失败";
    emit("state", "offline");
  };
  const { width, height } = dimensions();
  client.connect(new URLSearchParams({ token: authToken(), width: String(width), height: String(height), dpi: "96" }).toString());
}

async function toggleFullscreen() {
  if (!viewport.value) return;
  if (document.fullscreenElement) await document.exitFullscreen(); else await viewport.value.requestFullscreen();
  window.setTimeout(resize, 80);
}

onMounted(async () => {
  await nextTick();
  resizeObserver = new ResizeObserver(resize);
  if (viewport.value) resizeObserver.observe(viewport.value);
  connect();
});
watch(() => props.active, active => { if (active) nextTick(() => { resize(); viewport.value?.focus(); }); });
onBeforeUnmount(() => { resizeObserver?.disconnect(); disconnect(); });
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
