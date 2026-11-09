<script setup lang="ts">
import { onMounted, ref, watch, nextTick } from 'vue';
import { DatePicker } from '@arco-design/web-vue';
const props = defineProps<{ modelValue?: string; label: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const root = ref<HTMLElement>(), invalid=ref(false);
function labelInput() { root.value?.querySelector('input')?.setAttribute('aria-label', props.label); }
function commitText(event:Event){
  const input=event.target;
  if(!(input instanceof HTMLInputElement))return;
  const value=input.value.trim().replace(' ','T');
  const date=new Date(value);
  const valid=!value||(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)&&Number.isFinite(date.getTime())&&date.getDate()===Number(value.slice(8,10)));
  invalid.value=!valid;input.setCustomValidity(valid?'':'请输入有效的日期和时间');
  // Arco's time panel normally requires its confirm button. Typed values also
  // commit on input/blur, so submitting the surrounding form cannot lose them.
  if(valid)emit('update:modelValue',value);
}
function update(value:unknown){invalid.value=false;root.value?.querySelector('input')?.setCustomValidity('');emit('update:modelValue',String(value||''));}
onMounted(labelInput);
watch(() => props.label, () => void nextTick(labelInput));
</script>
<template><div ref="root" class="date-time-field" @input.capture="commitText" @blur.capture="commitText"><DatePicker :model-value="modelValue || undefined" :error="invalid" show-time format="YYYY-MM-DDTHH:mm" value-format="YYYY-MM-DDTHH:mm" allow-clear @update:model-value="update" /><small v-if="invalid" role="alert">请输入有效的日期和时间</small></div></template>
<style scoped>.date-time-field,.date-time-field>.arco-picker{width:100%;min-width:0}.date-time-field small{display:block;margin-top:6px;color:rgb(var(--danger-6))}</style>
