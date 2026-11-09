<script setup lang="ts">
import { computed, ref, useAttrs, watch, type HTMLAttributes, type StyleValue } from 'vue';
import { Input, InputNumber, Textarea } from '@arco-design/web-vue';
defineOptions({ inheritAttrs: false });
const props = withDefaults(defineProps<{ modelValue?: string | number; value?: string | number; type?: string; multiline?: boolean; modelModifiers?: { trim?: boolean; number?: boolean } }>(), {type:'text',modelValue:undefined,value:undefined,modelModifiers:()=>({})});
const emit = defineEmits<{ 'update:modelValue': [value: string | number]; input: [event: Event]; change: [event: Event]; blur: [event: FocusEvent] }>();
const attrs = useAttrs(), root = ref<HTMLElement>();
const nativeNames = new Set(['id','name','required','pattern','autocomplete','autofocus','spellcheck','inputmode','min','max','step','maxlength','rows']);
const nativeAttrs = computed(() => Object.fromEntries(Object.entries(attrs).filter(([key]) => nativeNames.has(key) || key.startsWith('aria-'))));
const componentAttrs = computed(() => Object.fromEntries(Object.entries(attrs).filter(([key]) => key !== 'class' && key !== 'style' && !nativeNames.has(key) && !key.startsWith('aria-'))));
const rootAttrs = computed(() => ({class:attrs.class as HTMLAttributes['class'],style:attrs.style as StyleValue}));
const localValue=ref<string|number>(props.modelValue??props.value??'');
watch(()=>props.modelValue??props.value,next=>{localValue.value=next??'';});
const displayValue = computed(() => localValue.value);
function update(next: string | number | undefined) {
  let output: string | number = props.modelModifiers?.number ? Number(next ?? 0) : String(next ?? '');
  if (typeof output === 'string' && props.modelModifiers?.trim) output = output.trim();
  localValue.value=output;
  emit('update:modelValue', output);
}
function nativeEvent(kind: 'input' | 'change', event?: Event) {
  const input = root.value?.querySelector('input,textarea');
  if (event) { if(kind==='input')emit('input',event);else emit('change',event); }
  else if (input) { const generated = new Event(kind); Object.defineProperty(generated, 'target', { value: input }); if(kind==='input')emit('input',generated);else emit('change',generated); }
}
function focus() { root.value?.querySelector<HTMLElement>('input,textarea')?.focus(); }
function select() { root.value?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input,textarea')?.select(); }
defineExpose({focus,select});
</script>
<template>
  <span ref="root" v-bind="rootAttrs" class="workbench-input">
    <Textarea v-if="multiline" v-bind="componentAttrs" :model-value="String(displayValue)" :textarea-attrs="nativeAttrs" @update:model-value="update" @input="(_value, event) => nativeEvent('input',event)" @change="(_value, event) => nativeEvent('change',event)" @blur="emit('blur',$event)" />
    <InputNumber v-else-if="type==='number'" model-event="input" v-bind="componentAttrs" :model-value="displayValue===''?undefined:Number(displayValue)" :min="attrs.min===undefined?undefined:Number(attrs.min)" :max="attrs.max===undefined?undefined:Number(attrs.max)" :step="attrs.step===undefined?1:Number(attrs.step)" :input-attrs="nativeAttrs" @update:model-value="update" @input="nativeEvent('input')" @change="nativeEvent('change')" @blur="emit('blur',$event)" />
    <Input v-else v-bind="componentAttrs" :model-value="String(displayValue)" :type="type==='password'?'password':'text'" :input-attrs="nativeAttrs" @update:model-value="update" @input="(_value,event)=>nativeEvent('input',event)" @change="(_value,event)=>nativeEvent('change',event)" @blur="emit('blur',$event)">
      <template v-if="$slots.prefix" #prefix><slot name="prefix" /></template>
    </Input>
  </span>
</template>
<style scoped>.workbench-input{display:inline-flex;min-width:0;width:100%}.workbench-input>.arco-textarea-wrapper,.workbench-input>.arco-input-wrapper{width:100%}</style>
