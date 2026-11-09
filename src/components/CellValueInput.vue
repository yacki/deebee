<script setup lang="ts">
import { computed } from 'vue';
import { columnType, enumOptions, isBinary, type CellColumn } from '../cellValues';
const props = defineProps<{ modelValue: string; column: CellColumn; disabled?: boolean; label: string; compact?: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const options = computed(() => enumOptions(props.column));
const kind = computed(() => columnType(props.column));
const boolean = computed(() => /^(bool|boolean|tinyint\(1\)|bit\(1\))/.test(kind.value));
const multiline = computed(() => !props.compact && (/json|text|clob/.test(kind.value) || isBinary(props.column)));
const hint = computed(() => isBinary(props.column) ? '0x 开头的十六进制，例如 0x00FF' : /^json/.test(kind.value) ? '有效 JSON；JSON null 与数据库 NULL 分开设置' : /datetime|timestamp/.test(kind.value) ? 'YYYY-MM-DD HH:mm:ss.ffffff' : /^date/.test(kind.value) ? 'YYYY-MM-DD' : /^time/.test(kind.value) ? 'HH:mm:ss.ffffff，可使用负数和超过 24 小时的时长' : /decimal|numeric|bigint/.test(kind.value) ? '按原始精度保存，不转换为浮点数' : '允许输入空字符串');
</script>
<template>
  <WorkbenchSelect v-if="options.length || boolean" :model-value="modelValue" :aria-label="label" :disabled="disabled" @update:model-value="emit('update:modelValue', String($event))"><a-option v-if="boolean" value="1">true / 1</a-option><a-option v-if="boolean" value="0">false / 0</a-option><a-option v-for="option in options" :key="option" :value="option">{{ option }}</a-option></WorkbenchSelect>
  <a-textarea v-else-if="multiline" :model-value="modelValue" :textarea-attrs="{'aria-label':label}" :disabled="disabled" :placeholder="hint" :auto-size="{minRows:6,maxRows:18}" @update:model-value="emit('update:modelValue', $event)" />
  <WorkbenchInput v-else :model-value="modelValue" :aria-label="label" :disabled="disabled" :placeholder="hint" @update:model-value="emit('update:modelValue', String($event))" />
</template>
