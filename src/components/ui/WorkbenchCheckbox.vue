<script setup lang="ts">
import { Checkbox } from '@arco-design/web-vue';
const props=defineProps<{modelValue?:unknown;checked?:boolean}>();
const emit=defineEmits<{'update:modelValue':[value:boolean];change:[event:Event]}>();
function change(value:boolean|Array<string|number|boolean>){const checked=!!value;emit('update:modelValue',checked);const event=new Event('change');Object.defineProperty(event,'target',{value:{checked}});emit('change',event);}
</script>
<template><Checkbox :model-value="Boolean(props.modelValue??props.checked)" @change="change"><slot /></Checkbox></template>
