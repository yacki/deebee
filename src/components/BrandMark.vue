<script setup lang="ts">
import mark from "../assets/deebee-mark.svg";
import electricMark from "../assets/deebee-mark-electric.svg";
defineProps<{ variant?: "electric" }>();
</script>

<template>
  <img class="deebee-mark" :src="variant==='electric'?electricMark:mark" alt="DeeBee" width="32" height="32" draggable="false" />
</template>

<style scoped>
.deebee-mark { display: block; width: 32px; height: 32px; flex: none; }
</style>
