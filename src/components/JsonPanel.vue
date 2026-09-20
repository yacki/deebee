<script setup lang="ts">
import { computed, ref, watch } from 'vue';
const props = defineProps<{ value?: unknown; modelValue?: string; editable?: boolean; truncated?: boolean; label?: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const mode = ref('formatted');
const warning = ref('');
const raw = computed(() => props.editable ? props.modelValue || '' : typeof props.value === 'string' ? props.value : JSON.stringify(props.value ?? null));
watch(raw,()=>{warning.value='';});
const parsed = computed(() => { try { return { valid: true, value: JSON.parse(raw.value) }; } catch { return { valid: false, value: null }; } });
const display = computed(() => mode.value === 'raw' || !parsed.value.valid ? raw.value : JSON.stringify(parsed.value.value, null, mode.value === 'formatted' ? 2 : undefined));
function format(next: string) {
  warning.value = '';
  if (!parsed.value.valid) { warning.value = '内容不是完整 JSON，保留原文，不能格式化。'; return; }
  mode.value = next;
  if (props.editable) emit('update:modelValue', JSON.stringify(parsed.value.value, null, next === 'formatted' ? 2 : undefined));
}
</script>
<template>
  <div class="json-panel">
    <div class="json-toolbar"><span>{{ label || 'JSON / 原文' }}</span><a-space size="mini"><a-button size="mini" @click="format('formatted')">格式化</a-button><a-button size="mini" @click="format('compact')">压缩</a-button><a-button v-if="!editable" size="mini" @click="mode='raw'">原文</a-button></a-space></div>
    <a-alert v-if="truncated" type="warning">已达到归档上限，以下内容不完整；不能恢复已截断部分。</a-alert>
    <a-alert v-if="warning" type="warning">{{ warning }}</a-alert>
    <a-textarea v-if="editable" :model-value="modelValue" :aria-label="label || 'JSON 编辑器'" :auto-size="{minRows:5,maxRows:20}" @update:model-value="emit('update:modelValue',$event)" />
    <pre v-else tabindex="0">{{ display || '无内容' }}</pre>
  </div>
</template>
<style scoped>
.json-panel{min-width:0;margin:12px 0;border:1px solid var(--color-border-2);border-radius:4px;overflow:hidden}.json-toolbar{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--color-fill-1);font-size:12px;gap:12px}.json-panel pre{margin:0;padding:14px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:460px;overflow:auto;font:12px/1.7 ui-monospace,monospace;background:var(--color-bg-1)}
</style>
