<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Icon } from '@iconify/vue';
import { elapsed } from '../recordings/player';
const props = defineProps<{ position: number; duration: number; playing: boolean; loading: boolean; seeking: boolean; error: string; protocol: string; speed: number }>();
const emit = defineEmits<{ play: []; pause: []; seek: [position: number, resume: boolean]; speed: [value: number] }>();
const root = ref<HTMLElement>(), dragging = ref(false), preview = ref(0);
const value = computed(() => dragging.value ? preview.value : props.position);
const disabled = computed(() => props.loading || !!props.error || !props.duration);
let resume = false, releaseTimer: ReturnType<typeof setTimeout> | undefined;
function begin(event: MouseEvent | TouchEvent) {
  if (disabled.value || ('button' in event && event.button !== 0)) return;
  if (releaseTimer) clearTimeout(releaseTimer);
  if (dragging.value) return;
  resume = props.playing; preview.value = props.position; dragging.value = true; emit('pause');
}
function update(next: number | [number, number]) {
  const target = Array.isArray(next) ? next[1] : next;
  if (dragging.value) preview.value = target;
  else emit('seek', target, props.playing);
}
function finish() {
  // Include the track click dispatched after mouseup in the final target.
  releaseTimer = setTimeout(() => {
    if (!dragging.value) return;
    const target = preview.value; dragging.value = false; emit('seek', target, resume);
  }, 0);
}
function cancel() {
  if (!dragging.value) return;
  dragging.value = false;
  if (resume) emit('play');
}
function key(event: KeyboardEvent) {
  if (disabled.value) return;
  const targets: Record<string, number> = { ArrowLeft: value.value - 5000, ArrowDown: value.value - 5000, ArrowRight: value.value + 5000, ArrowUp: value.value + 5000, Home: 0, End: props.duration };
  const target = targets[event.key];
  if (target === undefined) return;
  event.preventDefault(); emit('seek', Math.max(0, Math.min(props.duration, target)), props.playing);
}
function labelSlider() { root.value?.querySelector('[role=slider]')?.setAttribute('aria-label', '回放进度'); }
watch(() => props.loading, () => void nextTick(labelSlider));
onMounted(() => { labelSlider(); window.addEventListener('mouseup', finish); window.addEventListener('touchend', finish); window.addEventListener('touchcancel', cancel); window.addEventListener('blur', cancel); });
onBeforeUnmount(() => { if (releaseTimer) clearTimeout(releaseTimer); window.removeEventListener('mouseup', finish); window.removeEventListener('touchend', finish); window.removeEventListener('touchcancel', cancel); window.removeEventListener('blur', cancel); });
</script>

<template>
  <footer ref="root" class="playback-controls" aria-label="回放控制">
    <a-tooltip :content="playing ? '暂停' : '播放'"><IconButton :label="playing ? '暂停' : '播放'" :disabled="disabled || seeking" type="primary" shape="circle" @click="playing ? emit('pause') : emit('play')"><Icon :icon="playing ? 'lucide:pause' : 'lucide:play'" /></IconButton></a-tooltip>
    <div class="playback-timeline" @mousedown.capture="begin" @touchstart.capture.passive="begin" @keydown="key">
      <a-slider :model-value="value" :max="Math.max(duration, 1)" :step="1" :disabled="disabled" :format-tooltip="elapsed" @update:model-value="update" />
    </div>
    <output aria-label="回放时间">{{ elapsed(value) }} / {{ elapsed(duration) }}</output>
    <span v-if="seeking" role="status" class="playback-seeking"><a-spin :size="14" />正在定位…</span>
    <a-select v-if="protocol === 'ssh'" :model-value="speed" aria-label="回放速度" role="combobox" :disabled="disabled || seeking" :options="[0.5, 1, 2, 4].map(value => ({ value, label: `${value} 倍` }))" @change="emit('speed', Number($event))" />
    <a-tooltip content="跳至结尾"><IconButton label="跳至结尾" :disabled="disabled" shape="circle" @click="emit('seek', duration, false)"><Icon icon="lucide:skip-forward" /></IconButton></a-tooltip>
  </footer>
</template>

<style scoped>
.playback-controls{display:flex;align-items:center;gap:16px;padding:14px 18px;background:var(--color-bg-1);border-top:1px solid var(--color-border-2)}.playback-timeline{flex:1;min-width:90px;padding:8px 0}.playback-controls output{font-size:var(--deebee-font-size);font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--color-text-2)}.playback-controls>.arco-select{width:92px}.playback-seeking{display:flex;align-items:center;gap:6px;color:var(--color-text-3);font-size:var(--deebee-font-size);white-space:nowrap}.playback-controls svg{width:16px;height:16px}@media(max-width:760px){.playback-controls{flex-wrap:wrap;gap:12px}.playback-timeline{min-width:calc(100% - 64px)}.playback-controls output{margin-right:auto}}
</style>
