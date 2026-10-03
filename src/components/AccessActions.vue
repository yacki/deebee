<script setup lang="ts">
import { actionsForResource } from '../accessPolicy';
const selected = defineModel<string[]>({ required: true });
defineProps<{ resourceType?: string }>();
</script>

<template>
  <a-checkbox-group v-model="selected" class="action-choices">
    <a-checkbox v-for="action in actionsForResource(resourceType)" :key="action.value" :value="action.value">
      {{ action.label }}<small v-if="action.warning">需谨慎授权</small>
    </a-checkbox>
  </a-checkbox-group>
</template>

<style scoped>
.action-choices{display:flex;flex-wrap:wrap;gap:14px}.action-choices label{display:flex;flex-direction:row;align-items:center;gap:8px}.action-choices input{width:16px;height:16px}.action-choices small{color:#96631d}
</style>
