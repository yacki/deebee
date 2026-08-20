<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import type { Column } from "../types";

const props = withDefaults(defineProps<{ columns: Column[] | { name: string; type?: string; data_type?: string; nullable?: boolean }[]; rows: Record<string, unknown>[]; primaryKey?: string[]; editable?: boolean; storageKey?: string; selected?: number | null }>(), { primaryKey: () => [], editable: false, storageKey: "grid", selected: null });
const emit = defineEmits<{ save: [rowIndex: number, changes: Record<string, unknown>]; select: [rowIndex: number]; menu: [event: MouseEvent, rowIndex: number, column: string]; sort: [column: string]; }>();
const widths = reactive<Record<string, number>>({});
const editing = ref<{ row: number; column: string }>();
const draft = ref("");
const input = ref<HTMLInputElement>();
const shell = ref<HTMLElement>();
const selectedRows = ref(new Set<number>());
const rowHeight = ref(30);
const frozenThrough = ref(-1);
const columns = computed(() => props.columns || []);

onMounted(loadWidths);
watch(() => props.storageKey, loadWidths);
function loadWidths() { try { Object.assign(widths, JSON.parse(localStorage.getItem(`deebee_widths:${props.storageKey}`) || "{}")); } catch { /* ignored */ } }
function width(name: string) { return Math.max(80, widths[name] || 180); }
function resize(event: PointerEvent, name: string) {
  event.preventDefault(); event.stopPropagation();
  const start = event.clientX; const initial = width(name);
  const move = (next: PointerEvent) => { widths[name] = Math.max(80, Math.min(600, initial + next.clientX - start)); };
  const up = () => { localStorage.setItem(`deebee_widths:${props.storageKey}`, JSON.stringify(widths)); document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); };
  document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
}
function adjustWidth(name: string, delta: number) { widths[name] = Math.max(80, Math.min(600, width(name) + delta)); localStorage.setItem(`deebee_widths:${props.storageKey}`, JSON.stringify(widths)); }
function setInput(element: unknown) { input.value = element instanceof HTMLInputElement ? element : undefined; }
function setWidth(name: string, value: number) { widths[name] = Math.max(80, Math.min(600, value)); localStorage.setItem(`deebee_widths:${props.storageKey}`, JSON.stringify(widths)); }
function autoFit(name: string) { const longest = Math.max(name.length, ...props.rows.slice(0, 250).map(row => display(row[name]).length)); setWidth(name, Math.min(600, Math.max(96, longest * 8 + 38))); }
function autoFitAll() { for (const column of columns.value) autoFit(column.name); }
function setRowHeight(value: number) { rowHeight.value = Math.max(24, Math.min(120, value)); }
function freeze(column: string) { frozenThrough.value = columns.value.findIndex(item => item.name === column); }
function unfreeze() { frozenThrough.value = -1; }
function selectAll() { selectedRows.value = new Set(props.rows.map((_, index) => index)); }
function deselectAll() { selectedRows.value = new Set(); }
function isSelected(index: number) { return props.selected === index || selectedRows.value.has(index); }
function toggleSelected(index: number) { if (props.editable) return; const next = new Set(selectedRows.value); if (next.has(index)) next.delete(index); else next.add(index); selectedRows.value = next; }
function stickyStyle(index: number) { if (index > frozenThrough.value) return {}; let left = 44; for (let current = 0; current < index; current++) left += width(columns.value[current].name); return { position: "sticky" as const, left: `${left}px`, zIndex: 3 }; }
function goToRow(row: number) { shell.value?.querySelector<HTMLElement>(`[data-row-index="${Math.max(0, Math.min(props.rows.length - 1, row))}"]`)?.scrollIntoView({ block: "center" }); }
async function edit(row: number, column: string, value: unknown) {
  if (!props.editable || props.primaryKey.includes(column)) return;
  editing.value = { row, column }; draft.value = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
  await nextTick(); input.value?.select();
}
function commit() {
  if (!editing.value) return;
  const { row, column } = editing.value; const source = props.rows[row]?.[column];
  let value: unknown = draft.value;
  if (draft.value === "" && (columns.value.find(item => item.name === column)?.nullable ?? false)) value = null;
  else if (typeof source === "number" && draft.value !== "") value = Number(draft.value);
  editing.value = undefined;
  if (value !== source) emit("save", row, { [column]: value });
}
function display(value: unknown) { if (value === null) return "NULL"; if (typeof value === "object") return JSON.stringify(value); return String(value ?? ""); }
defineExpose({ setWidth, autoFit, autoFitAll, setRowHeight, freeze, unfreeze, selectAll, deselectAll, goToRow });
</script>

<template>
  <div ref="shell" class="editable-grid-shell" role="grid" tabindex="0">
    <table>
      <colgroup><col class="row-number-col" /><col v-for="column in columns" :key="column.name" :style="{ width: `${width(column.name)}px` }" /></colgroup>
      <thead>
        <tr>
          <th class="row-number">#</th><th v-for="(column, columnIndex) in columns" :key="column.name" :class="{ frozen: columnIndex <= frozenThrough }" :style="stickyStyle(columnIndex)" @click="emit('sort', column.name)">
            <span class="column-title"><Icon v-if="primaryKey.includes(column.name)" icon="lucide:key-round" class="key-icon" />{{ column.name }}</span>
            <small>{{ 'data_type' in column ? column.data_type : column.type }}</small>
            <i class="resize-handle" role="separator" tabindex="0" :aria-label="`调整 ${column.name} 列宽`" @pointerdown="resize($event, column.name)" @keydown.left.prevent="adjustWidth(column.name,-16)" @keydown.right.prevent="adjustWidth(column.name,16)" />
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, rowIndex) in rows" :key="rowIndex" :data-row-index="rowIndex" :class="{ selected: isSelected(rowIndex) }" :style="{ height: `${rowHeight}px` }" @click="emit('select', rowIndex);toggleSelected(rowIndex)">
          <td class="row-number">{{ rowIndex + 1 }}</td>
          <td v-for="(column, columnIndex) in columns" :key="column.name" :class="{ null: row[column.name] === null, editable: editable && !primaryKey.includes(column.name), editing: editing?.row === rowIndex && editing?.column === column.name, frozen: columnIndex <= frozenThrough }" :style="stickyStyle(columnIndex)" :title="editable ? '双击原地编辑' : display(row[column.name])" @dblclick="edit(rowIndex, column.name, row[column.name])" @contextmenu.prevent="emit('menu', $event, rowIndex, column.name)">
            <input v-if="editing?.row === rowIndex && editing?.column === column.name" :ref="setInput" v-model="draft" :aria-label="`编辑 ${column.name}`" @keydown.enter.prevent="commit" @keydown.esc.prevent="editing = undefined" @blur="commit" />
            <span v-else>{{ display(row[column.name]) }}</span>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="!rows.length" class="empty-grid"><Icon icon="lucide:database-zap" /><span>没有数据</span></div>
  </div>
</template>
