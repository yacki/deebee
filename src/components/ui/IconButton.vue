<script setup lang="ts">
import { Button, Tooltip } from '@arco-design/web-vue';
defineOptions({inheritAttrs:false});
defineProps<{label:string;type?:'primary'|'secondary'|'outline'|'dashed'|'text';shape?:'square'|'round'|'circle'}>();
</script>
<template><Tooltip :content="label"><Button v-bind="$attrs" :aria-label="label" :type="type||'text'" :shape="shape||'square'"><slot /></Button></Tooltip></template>
