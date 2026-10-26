<script setup lang="ts">
import { Modal } from '@arco-design/web-vue';
import { nextTick, onMounted, onUpdated, useId, useAttrs } from 'vue';
defineOptions({inheritAttrs:false});
const props=defineProps<{title?:string}>(),attrs=useAttrs();
const className=`workbench-modal-${useId().replaceAll(':','')}`;
function decorate(){const dialog=document.querySelector(`.${className}`);if(!dialog)return;dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');if(props.title)dialog.setAttribute('aria-label',props.title);dialog.querySelector('[aria-label=Close]')?.setAttribute('aria-label','关闭');}
onMounted(()=>void nextTick(decorate));onUpdated(()=>void nextTick(decorate));
</script>
<template><Modal v-bind="$attrs" :title="title" :modal-class="[className,attrs.modalClass||attrs['modal-class']]" @open="decorate"><template v-for="(_,name) in $slots" #[name]="slot"><slot :name="name" v-bind="slot||{}" /></template></Modal></template>
