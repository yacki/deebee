<script setup lang="ts">
import { Select } from '@arco-design/web-vue';
const props=defineProps<{modelValue?:string|number;value?:string|number}>();
const emit=defineEmits<{'update:modelValue':[value:string|number];change:[event:Event]}>();
function change(value:unknown){const next=typeof value==='string'||typeof value==='number'?value:'';emit('update:modelValue',next);const event=new Event('change');Object.defineProperty(event,'target',{value:{value:String(next)}});emit('change',event);}
</script>
<template><Select :model-value="props.modelValue??props.value" role="combobox" @change="change"><slot /></Select></template>
