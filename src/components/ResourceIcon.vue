<script setup lang="ts">
import { computed } from 'vue';
import { Icon } from '@iconify/vue';
import { resourceProduct } from '../resourcePresentation';
const props = withDefaults(defineProps<{ type: string; size?: number }>(), { size: 32 });
const product = computed(() => resourceProduct(props.type));
</script>

<template>
  <span class="resource-icon" :data-resource-type="type" :style="{width:size+'px',height:size+'px'}">
    <img v-if="product.icon" :src="product.icon" :alt="product.alt" :width="size" :height="size" />
    <Icon v-else icon="lucide:server" :width="size" :height="size" aria-label="资源" />
  </span>
</template>

<style scoped>
.resource-icon{display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;vertical-align:middle}.resource-icon img{display:block;object-fit:contain;max-width:100%;max-height:100%}
</style>
