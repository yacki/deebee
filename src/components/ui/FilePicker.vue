<script setup lang="ts">
import { ref } from 'vue';
import { Upload, Button, type FileItem } from '@arco-design/web-vue';
import { Icon } from '@iconify/vue';
defineProps<{accept?:string;label?:string}>();
const files=ref<FileItem[]>([]);
const emit=defineEmits<{change:[event:Event]}>();
function change(_files:FileItem[], file:FileItem){files.value=[];if(!file.file)return;const event=new Event('change');Object.defineProperty(event,'target',{value:{files:[file.file]}});emit('change',event);}
</script>
<template><Upload v-model:file-list="files" :accept="accept" :auto-upload="false" :show-file-list="false" :limit="1" @change="change"><Button :aria-label="label || '选择文件'"><Icon icon="lucide:upload" />{{ label || '选择文件' }}</Button></Upload></template>
