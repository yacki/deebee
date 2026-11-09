<script setup lang="ts">
import { Collapse, CollapseItem } from '@arco-design/web-vue';
withDefaults(defineProps<{ title: string; open?: boolean }>(), {open:false});
</script>
<template><Collapse :default-active-key="open ? ['content'] : []" :bordered="false" class="disclosure-panel"><CollapseItem :header="title" item-key="content"><slot /></CollapseItem></Collapse></template>
<style scoped>.disclosure-panel{background:transparent}.disclosure-panel :deep(.arco-collapse-item-content){background:transparent}.disclosure-panel :deep(.arco-collapse-item-header){background:var(--color-fill-1)}</style>
