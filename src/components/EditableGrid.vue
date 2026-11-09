<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, useId, watch } from "vue";
import type { Column } from "../types";
import { gridViewStates } from '../gridViewState';

const props = withDefaults(defineProps<{ columns: Column[] | { name: string; type?: string; data_type?: string; nullable?: boolean }[]; rows: Record<string, unknown>[]; primaryKey?: string[]; editable?: boolean; storageKey?: string; selected?: number | null }>(), { primaryKey: () => [], editable: false, storageKey: "grid", selected: null });
const emit = defineEmits<{ save: [rowIndex: number, changes: Record<string, unknown>]; select: [rowIndex: number | null]; menu: [event: MouseEvent, rowIndex: number, column: string]; sort: [column: string]; }>();
const widths = reactive<Record<string, number>>({});
const editing = shallowRef<{ row: number; column: string; rows: Record<string, unknown>[] }>();
const draft = ref("");
const input = ref<{select:()=>void}>();
const frame = ref<HTMLElement>();
const shell = ref<HTMLElement>();
const gridId = useId();
const currentCell = shallowRef<{ row: number; column: string }>();
let selectionAnchor = 0;
const selectedRows = ref(new Set<number>());
const rowHeight = ref(30);
const frozenThrough = ref(-1);
let suppressSortClick = false;
const columns = computed(() => props.columns || []);
const columnIndexes = computed(() => new Map(columns.value.map((column, index) => [column.name, index])));
const MIN_COLUMN_WIDTH = 24;
const MAX_COLUMN_WIDTH = 600;
const DEFAULT_COLUMN_WIDTH = 180;
const tableWidth = computed(() => 44 + columns.value.reduce((total, column) => total + width(column.name), 0));
const scrollTop = ref(0);
const viewportHeight = ref(400);
const HEADER_HEIGHT = 34;
const printing = ref(false);
const OVERSCAN = 12;
const virtualized = computed(() => props.rows.length > 200 && !printing.value);
const windowSize = computed(() => Math.ceil(viewportHeight.value / rowHeight.value) + OVERSCAN * 2);
const firstRow = computed(() => virtualized.value ? Math.max(0, Math.min(props.rows.length - windowSize.value, Math.floor(scrollTop.value / rowHeight.value) - OVERSCAN)) : 0);
const lastRow = computed(() => virtualized.value ? Math.min(props.rows.length, firstRow.value + windowSize.value) : props.rows.length);
const visibleRows = computed(() => props.rows.slice(firstRow.value, lastRow.value).map((row, offset) => ({ row, index: firstRow.value + offset })));
const topPadding = computed(() => firstRow.value * rowHeight.value);
const bottomPadding = computed(() => (props.rows.length - lastRow.value) * rowHeight.value);
const activeDescendant = computed(() => {
  const cell = currentCell.value;
  return cell && cell.row >= firstRow.value && cell.row < lastRow.value ? cellId(cell.row, cell.column) : undefined;
});
let resizeObserver: ResizeObserver | undefined;

function measureViewport() {
  if (!shell.value) return;
  viewportHeight.value = Math.max(0, shell.value.clientHeight - HEADER_HEIGHT) || 400;
}
function scroll() {
  scrollTop.value = shell.value?.scrollTop || 0;
}
watch([firstRow, lastRow], () => {
  if (editing.value && (editing.value.row < firstRow.value || editing.value.row >= lastRow.value)) commit();
});
function rememberView(rows = props.rows) {
  if (!rows.length) return;
  gridViewStates.set(rows, { cell: currentCell.value, selected: selectedRows.value, anchor: selectionAnchor, top: shell.value?.scrollTop || 0, left: shell.value?.scrollLeft || 0, rowHeight: rowHeight.value, frozenThrough: frozenThrough.value });
}
function restoreView(rows = props.rows) {
  const saved = gridViewStates.get(rows);
  editing.value = undefined;
  currentCell.value = saved?.cell;
  selectedRows.value = saved ? new Set(saved.selected) : new Set();
  selectionAnchor = saved?.anchor || 0;
  rowHeight.value = saved?.rowHeight || 30;
  frozenThrough.value = saved?.frozenThrough ?? -1;
  scrollTop.value = saved?.top || 0;
  void nextTick(() => {
    if (!shell.value || props.rows !== rows) return;
    shell.value.scrollTop = saved?.top || 0; shell.value.scrollLeft = saved?.left || 0;
    scroll();
  });
}
onMounted(() => {
  loadWidths();restoreView();
  measureViewport();
  resizeObserver = new ResizeObserver(measureViewport);
  if (shell.value) resizeObserver.observe(shell.value);
});
onBeforeUnmount(() => { rememberView();resizeObserver?.disconnect(); });

function loadWidths() {
  for(const name of Object.keys(widths))delete widths[name];
  try { Object.assign(widths, JSON.parse(localStorage.getItem(`deebee_widths:${props.storageKey}`) || "{}")); } catch { /* ignored */ }
}
watch([() => props.rows, () => props.storageKey], ([rows], [previousRows]) => {
  rememberView(previousRows);loadWidths();restoreView(rows);
});
function clampWidth(value: number) { return Math.max(MIN_COLUMN_WIDTH, Math.min(MAX_COLUMN_WIDTH, value)); }
function width(name: string) { return clampWidth(widths[name] ?? DEFAULT_COLUMN_WIDTH); }
function resize(event: PointerEvent, name: string) {
  event.preventDefault(); event.stopPropagation();
  suppressSortClick = true;
  const start = event.clientX; const initial = width(name);
  const move = (next: PointerEvent) => { widths[name] = clampWidth(initial + next.clientX - start); };
  const finish = () => {
    localStorage.setItem(`deebee_widths:${props.storageKey}`, JSON.stringify(widths));
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", finish);
    document.removeEventListener("pointercancel", finish);
    window.setTimeout(() => { suppressSortClick = false; }, 0);
  };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", finish);
  document.addEventListener("pointercancel", finish);
}
function sort(event: MouseEvent, name: string) {
  if (suppressSortClick || (event.target as Element).closest(".resize-handle")) return;
  emit("sort", name);
}
function adjustWidth(name: string, delta: number) { widths[name] = clampWidth(width(name) + delta); localStorage.setItem(`deebee_widths:${props.storageKey}`, JSON.stringify(widths)); }
function setInput(element: unknown) { input.value = element && typeof (element as {select?:unknown}).select === 'function' ? element as {select:()=>void} : undefined; }
function setWidth(name: string, value: number) { widths[name] = clampWidth(value); localStorage.setItem(`deebee_widths:${props.storageKey}`, JSON.stringify(widths)); }
function autoFit(name: string) { const longest = Math.max(name.length, ...props.rows.slice(0, 250).map(row => display(row[name]).length)); setWidth(name, Math.min(600, Math.max(96, longest * 8 + 38))); }
function autoFitAll() { for (const column of columns.value) autoFit(column.name); }
function setRowHeight(value: number) {
  const wasScrolled = scrollTop.value > 0;
  const anchor = scrollTop.value / rowHeight.value;
  rowHeight.value = Math.max(24, Math.min(120, value));
  void nextTick(() => { if (shell.value) { shell.value.scrollTop = wasScrolled ? anchor * rowHeight.value : 0; scroll(); } });
}
function freeze(column: string) { frozenThrough.value = columns.value.findIndex(item => item.name === column); }
function unfreeze() { frozenThrough.value = -1; }
function selectAll() { selectedRows.value = new Set(props.rows.map((_, index) => index)); }
function deselectAll() { selectedRows.value = new Set(); currentCell.value = undefined; selectionAnchor = 0; emit("select", null); }
function isSelected(index: number) { return props.selected === index || selectedRows.value.has(index); }
function cellId(row: number, column: string) { return `${gridId}-cell-${row}-${columnIndexes.value.get(column)}`; }
function isCurrent(row: number, column: string) { return currentCell.value?.row === row && currentCell.value.column === column; }
function focusGrid() { frame.value?.focus({ preventScroll: true }); }
function chooseCell(row: number, column: string, event?: MouseEvent, preserveSelection = false) {
  if (editing.value?.row === row && editing.value.column === column) return;
  if (editing.value) commit();
  currentCell.value = { row, column };
  if (!preserveSelection) {
    if (event?.shiftKey) {
      const next = event.ctrlKey || event.metaKey ? new Set(selectedRows.value) : new Set<number>();
      for (let index = Math.min(selectionAnchor, row); index <= Math.max(selectionAnchor, row); index++) next.add(index);
      selectedRows.value = next;
    } else if (event?.ctrlKey || event?.metaKey) {
      const next = new Set(selectedRows.value); if (next.has(row)) next.delete(row); else next.add(row);
      selectedRows.value = next; selectionAnchor = row;
    } else { selectedRows.value = new Set([row]); selectionAnchor = row; }
    emit('select', selectedRows.value.has(row) ? row : null);
  }
  focusGrid();
}
function rowClick(row: number, event: MouseEvent) { const column = currentCell.value?.column || columns.value[0]?.name; if (column) chooseCell(row, column, event); }
function rowMenu(row: number, event: MouseEvent) { const column = currentCell.value?.column || columns.value[0]?.name; if (column) openMenu(event, row, column); }
function openMenu(event: MouseEvent, row: number, column: string) {
  chooseCell(row, column, undefined, isSelected(row));
  emit('select', row);
  emit('menu', event, row, column);
}
function revealCell(row: number, column: number) {
  const body = shell.value; if (!body) return;
  const top = row * rowHeight.value, bottom = top + rowHeight.value;
  if (top < body.scrollTop) body.scrollTop = top;
  else if (bottom > body.scrollTop + viewportHeight.value) body.scrollTop = bottom - viewportHeight.value;
  if (column > frozenThrough.value) {
    const left = 44 + columns.value.slice(0, column).reduce((sum, item) => sum + width(item.name), 0);
    const right = left + width(columns.value[column].name);
    const frozenWidth = 44 + columns.value.slice(0, frozenThrough.value + 1).reduce((sum, item) => sum + width(item.name), 0);
    if (left < body.scrollLeft + frozenWidth) body.scrollLeft = Math.max(0, left - frozenWidth);
    else if (right > body.scrollLeft + body.clientWidth) body.scrollLeft = right - body.clientWidth;
  }
  scroll();
}
function navigate(row: number, column: number, event?: KeyboardEvent) {
  row = Math.max(0, Math.min(props.rows.length - 1, row)); column = Math.max(0, Math.min(columns.value.length - 1, column));
  if (!props.rows.length || !columns.value[column]) return;
  const anchor = selectionAnchor;
  chooseCell(row, columns.value[column].name);
  if (event?.shiftKey && event.key !== 'Tab') {
    selectionAnchor = anchor;
    const next = new Set<number>();for (let index = Math.min(anchor, row); index <= Math.max(anchor, row); index++) next.add(index);
    selectedRows.value = next;
  }
  revealCell(row, column);
}
function tabCell(backward = false) {
  const cell = currentCell.value; if (!cell) return;
  const position = cell.row * columns.value.length + columns.value.findIndex(item => item.name === cell.column) + (backward ? -1 : 1);
  if (position < 0 || position >= props.rows.length * columns.value.length) return false;
  navigate(Math.floor(position / columns.value.length), position % columns.value.length);return true;
}
function key(event: KeyboardEvent) {
  if ((event.target as HTMLElement).closest('input,textarea,select,button') || !props.rows.length || !columns.value.length) return;
  const cell = currentCell.value || { row: 0, column: columns.value[0].name };
  const column = columns.value.findIndex(item => item.name === cell.column), command = event.ctrlKey || event.metaKey;
  let row = cell.row, col = column;
  if (command && event.key.toLowerCase() === 'a') { event.preventDefault(); selectAll(); return; }
  if (command && event.key.toLowerCase() === 'c' && currentCell.value) { event.preventDefault(); void navigator.clipboard?.writeText(display(props.rows[cell.row]?.[cell.column])).catch(() => undefined); return; }
  if (event.key === 'F2' || event.key === 'Enter') { event.preventDefault(); void edit(cell.row, cell.column, props.rows[cell.row]?.[cell.column]); return; }
  if (event.key === 'Tab') { if (tabCell(event.shiftKey)) event.preventDefault(); return; }
  if (event.key === 'ArrowUp') row--;
  else if (event.key === 'ArrowDown') row++;
  else if (event.key === 'ArrowLeft') col--;
  else if (event.key === 'ArrowRight') col++;
  else if (event.key === 'PageUp') row -= Math.max(1, Math.floor(viewportHeight.value / rowHeight.value));
  else if (event.key === 'PageDown') row += Math.max(1, Math.floor(viewportHeight.value / rowHeight.value));
  else if (event.key === 'Home') { col = 0; if (command) row = 0; }
  else if (event.key === 'End') { col = columns.value.length - 1; if (command) row = props.rows.length - 1; }
  else return;
  event.preventDefault();navigate(row, col, event);
}
function stickyStyle(index: number, header=false) { if (index > frozenThrough.value) return {}; let left = 44; for (let current = 0; current < index; current++) left += width(columns.value[current].name); return { position: "sticky" as const, left: `${left}px`, zIndex: header?6:3 }; }
async function goToRow(row: number) {
  const index = Math.max(0, Math.min(props.rows.length - 1, row));
  if (shell.value) {
    shell.value.scrollTop = Math.max(0, index * rowHeight.value - (viewportHeight.value - rowHeight.value) / 2);
    scroll();
    await nextTick();
  }
  const column = currentCell.value?.column || columns.value[0]?.name;
  if (column) { chooseCell(index, column, undefined, selectedRows.value.size > 0); emit('select', index); }
}
async function edit(row: number, column: string, value: unknown) {
  if (!props.editable || props.primaryKey.includes(column)) return;
  if (editing.value?.row === row && editing.value.column === column) return;
  chooseCell(row, column, undefined, isSelected(row));
  editing.value = { row, column, rows: props.rows }; draft.value = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
  await nextTick(); input.value?.select();
}
function commit(refocus = false) {
  if (!editing.value) return;
  if (editing.value.rows !== props.rows) { editing.value = undefined; return; }
  const { row, column } = editing.value; const source = props.rows[row]?.[column];
  let value: unknown = draft.value;
  if (draft.value === "" && (columns.value.find(item => item.name === column)?.nullable ?? false)) value = null;
  else if (typeof source === "number" && draft.value !== "") value = Number(draft.value);
  editing.value = undefined;
  if (value !== source) emit("save", row, { [column]: value });
  if (refocus) void nextTick(focusGrid);
}
function cancelEdit() { editing.value = undefined; void nextTick(focusGrid); }
function finishAndTab(event: KeyboardEvent) { commit(); if (!tabCell(event.shiftKey)) void nextTick(focusGrid); }
function display(value: unknown) { if (value === null) return "NULL"; if (typeof value === "object") return JSON.stringify(value); return String(value ?? ""); }
async function preparePrint() { printing.value = true; await nextTick(); }
function finishPrint() { printing.value = false; }
defineExpose({ setWidth, autoFit, autoFitAll, setRowHeight, freeze, unfreeze, selectAll, deselectAll, goToRow, preparePrint, finishPrint });
</script>

<template>
  <div ref="frame" class="editable-grid-frame" role="grid" :aria-rowcount="rows.length + 1" :aria-colcount="columns.length + 1" :aria-activedescendant="activeDescendant" tabindex="0" :style="{ '--grid-row-height': `${rowHeight}px` }" @keydown="key">
    <div ref="shell" class="editable-grid-shell" @scroll.passive="scroll">
      <table :style="{ width: `${tableWidth}px` }">
        <colgroup><col class="row-number-col" /><col v-for="column in columns" :key="column.name" :style="{ width: `${width(column.name)}px` }" /></colgroup>
        <thead class="grid-header">
          <tr>
            <th class="row-number">#</th><th v-for="(column, columnIndex) in columns" :key="column.name" :aria-label="column.name" :class="{ frozen: columnIndex <= frozenThrough, 'current-column': currentCell?.column === column.name }" :style="stickyStyle(columnIndex,true)" @click="sort($event, column.name)">
              <span class="column-title"><Icon v-if="primaryKey.includes(column.name)" icon="lucide:key-round" class="key-icon" />{{ column.name }}</span>
              <i class="resize-handle" role="separator" tabindex="0" :aria-label="`调整 ${column.name} 列宽`" aria-orientation="vertical" :aria-valuemin="MIN_COLUMN_WIDTH" :aria-valuemax="MAX_COLUMN_WIDTH" :aria-valuenow="width(column.name)" @click.stop @pointerdown="resize($event, column.name)" @keydown.left.stop.prevent="adjustWidth(column.name,-16)" @keydown.right.stop.prevent="adjustWidth(column.name,16)" />
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="topPadding" class="grid-spacer" aria-hidden="true"><td :colspan="columns.length + 1" :style="{ height: `${topPadding}px` }" /></tr>
          <tr v-for="{ row, index: rowIndex } in visibleRows" :key="rowIndex" :data-row-index="rowIndex" :aria-rowindex="rowIndex + 2" :aria-selected="isSelected(rowIndex)" :class="{ selected: isSelected(rowIndex), 'current-row': currentCell?.row === rowIndex }" :style="{ height: `${rowHeight}px` }">
            <td class="row-number" @click="rowClick(rowIndex,$event)" @contextmenu.prevent="rowMenu(rowIndex,$event)">{{ rowIndex + 1 }}</td>
            <td v-for="(column, columnIndex) in columns" :id="cellId(rowIndex,column.name)" :key="column.name" :data-column="column.name" :aria-selected="isCurrent(rowIndex,column.name)" :class="{ null: row[column.name] === null, editable: editable && !primaryKey.includes(column.name), editing: editing?.row === rowIndex && editing?.column === column.name, frozen: columnIndex <= frozenThrough, 'active-cell': isCurrent(rowIndex,column.name) }" :style="stickyStyle(columnIndex)" :title="editable ? '双击原地编辑' : display(row[column.name])" @click="chooseCell(rowIndex,column.name,$event)" @dblclick="edit(rowIndex,column.name,row[column.name])" @contextmenu.prevent="openMenu($event,rowIndex,column.name)">
              <WorkbenchInput v-if="editing?.row === rowIndex && editing?.column === column.name" :ref="setInput" v-model="draft" :aria-label="`编辑 ${column.name}`" @keydown.enter.stop.prevent="commit(true)" @keydown.esc.stop.prevent="cancelEdit" @keydown.tab.stop.prevent="finishAndTab" @blur="commit()" />
              <span v-else>{{ display(row[column.name]) }}</span>
            </td>
          </tr>
          <tr v-if="bottomPadding" class="grid-spacer" aria-hidden="true"><td :colspan="columns.length + 1" :style="{ height: `${bottomPadding}px` }" /></tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty-grid"><Icon icon="lucide:database-zap" /><span>没有数据</span></div>
    </div>
  </div>
</template>

<style scoped>
.editable-grid-frame { display:flex; flex:1; flex-direction:column; min-width:0; min-height:0; overflow:hidden; border:1px solid #c5ced9; background:#fff; outline:0; }
.editable-grid-frame:focus-visible { outline:1px solid #89a9ce; outline-offset:-1px; }
.editable-grid-frame table { table-layout:fixed; border-collapse:separate; border-spacing:0; }
.grid-header { position:sticky; top:0; z-index:6; background:#eef2f7; }
.grid-header th { height:34px; box-sizing:border-box; padding:0 9px; border-right:1px solid #cbd4df; border-bottom:1px solid #b9c4d2; background:#eef2f7; color:#435365; text-align:left; font-weight:600; white-space:nowrap; position:sticky; top:0; cursor:pointer; user-select:none; }
.grid-header .row-number { position:sticky; left:0; z-index:8; padding:0; text-align:center; color:#7c8b9b; }
.grid-header .current-column { background:#dce7f4; color:#1e4d80; box-shadow:inset 0 -2px #4e7faf; }
.editable-grid-shell { min-width:0; overflow:auto; overflow-y:scroll; scrollbar-gutter:stable; overflow-anchor:none; overscroll-behavior:contain; scroll-behavior:auto; }
.editable-grid-shell::-webkit-scrollbar { width:12px; height:12px; }
.editable-grid-shell::-webkit-scrollbar-track { background:#edf1f5; }
.editable-grid-shell::-webkit-scrollbar-thumb { border:3px solid #edf1f5; border-radius:0; background:#9aaaba; }
.editable-grid-shell::-webkit-scrollbar-thumb:hover { background:#71869b; }
.editable-grid-shell::-webkit-scrollbar-corner { background:#e5ebf2; }
.editable-grid-shell td { box-sizing:border-box; user-select:none; transition:none; }
.editable-grid-shell tbody tr:not(.grid-spacer) > td { height:var(--grid-row-height); }
.editable-grid-shell .grid-spacer > td { padding:0; border:0; }
.editable-grid-shell tbody tr:hover > td { background:#f2f6fb; }
.editable-grid-shell tbody tr.selected > td { background:#dce9f8; }
.editable-grid-shell tbody tr.selected > .row-number { background:#bfd4ee; color:#204e7e; font-weight:600; }
.editable-grid-shell .row-number { font-variant-numeric:tabular-nums; border-right-color:#c4cedb; cursor:default; }
.editable-grid-shell tr.current-row > .row-number::before { content:''; position:absolute; inset:0 auto 0 0; width:3px; background:#2868aa; }
.editable-grid-shell td.active-cell { background:#f8fbff !important; box-shadow:inset 0 0 0 2px #2868aa !important; color:#183e65; }
.editable-grid-shell td.editable:hover:not(.active-cell):not(.editing) { box-shadow:none; }
.editable-grid-shell td.editing :deep(.workbench-input) { height:calc(var(--grid-row-height) - 1px); vertical-align:top; }
.editable-grid-shell td.editing :deep(.arco-input-wrapper) { height:100%; min-height:0; border-radius:0; }
.editable-grid-shell td.editing :deep(input) { user-select:text; }
@media print {
  .editable-grid-frame { display:block; height:auto; overflow:visible; border:0; }
  .grid-header { display:table-header-group; position:static; }
  .grid-header th { position:static!important; }
  .editable-grid-shell { height:auto; overflow:visible!important; }
  .editable-grid-shell td.active-cell { box-shadow:none!important; }
}
</style>
