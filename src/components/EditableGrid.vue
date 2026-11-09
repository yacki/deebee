<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, useId, watch } from "vue";
import CellValueInput from './CellValueInput.vue';
import { cellText, formatCellJson, columnType, isGenerated, parseCellValue, parsePastedValue, parseDelimited, delimitedText, type GridEdit } from '../cellValues';
import type { Column } from "../types";
import { gridViewStates } from '../gridViewState';

const props = withDefaults(defineProps<{ columns: Column[] | { name: string; type?: string; data_type?: string; nullable?: boolean }[]; rows: Record<string, unknown>[]; primaryKey?: string[]; editable?: boolean; storageKey?: string; selected?: number | null; saveRows?: (edits: GridEdit[]) => Promise<void> }>(), { primaryKey: () => [], editable: false, storageKey: "grid", selected: null, saveRows: undefined });
const emit = defineEmits<{ save: [rowIndex: number, changes: Record<string, unknown>]; select: [rowIndex: number | null]; menu: [event: MouseEvent, rowIndex: number, column: string]; sort: [column: string]; export: [format: string, rows: Record<string, unknown>[], columns: string[]]; }>();
const widths = reactive<Record<string, number>>({});
const editing = shallowRef<{ row: number; column: string; rows: Record<string, unknown>[] }>();
const draft = ref("");
const input = ref<{select:()=>void}>();
const frame = ref<HTMLElement>();
const shell = ref<HTMLElement>();
const gridId = useId();
const currentCell = shallowRef<{ row: number; column: string }>();
const cellAnchor = shallowRef<{ row: number; column: string }>();
const rangeEnd = shallowRef<{ row: number; column: string }>();
const rowSelection = ref(false);
const viewMode = ref<'grid'|'record'|'text'|'json'>('grid');
const error = ref('');
const saving = ref(false);
const cellEditor = ref<{ row: number; column: string; value: string; nullValue: boolean }>();
const pastePreview = ref<{ text: string; edits: GridEdit[]; count: number }>();
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
  editing.value = undefined; cellEditor.value=undefined;pastePreview.value=undefined;error.value='';
  currentCell.value = saved?.cell; cellAnchor.value=saved?.cell;rangeEnd.value=saved?.cell;rowSelection.value=false;
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
function selectAll() { rowSelection.value = true; selectedRows.value = new Set(props.rows.map((_, index) => index)); }
function deselectAll() { selectedRows.value = new Set(); currentCell.value = undefined; cellAnchor.value = undefined; rangeEnd.value = undefined; rowSelection.value = false; selectionAnchor = 0; emit("select", null); }
function isSelected(index: number) { return props.selected === index || selectedRows.value.has(index); }
function cellId(row: number, column: string) { return `${gridId}-cell-${row}-${columnIndexes.value.get(column)}`; }
function isCurrent(row: number, column: string) { return currentCell.value?.row === row && currentCell.value.column === column; }
function focusGrid() { frame.value?.focus({ preventScroll: true }); }
function chooseCell(row: number, column: string, event?: MouseEvent, preserveSelection = false) {
  if (editing.value?.row === row && editing.value.column === column) return;
  if (editing.value) commit();
  const nextCell = { row, column };
  if (!event?.shiftKey || !cellAnchor.value) cellAnchor.value = nextCell;
  rowSelection.value = Boolean(event?.ctrlKey || event?.metaKey);
  currentCell.value = nextCell; rangeEnd.value=nextCell;
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
function rowClick(row: number, event: MouseEvent) { const column = currentCell.value?.column || columns.value[0]?.name; if (column) { chooseCell(row, column, event); rowSelection.value = true; } }
function rowMenu(row: number, event: MouseEvent) { const column = currentCell.value?.column || columns.value[0]?.name; if (column) openMenu(event, row, column); }
function openMenu(event: MouseEvent, row: number, column: string) {
  const keep=inSelection(row,column),anchor=cellAnchor.value,end=rangeEnd.value,rowsMode=rowSelection.value;
  chooseCell(row, column, undefined, isSelected(row));
  if(keep){cellAnchor.value=anchor;rangeEnd.value=end;rowSelection.value=rowsMode;}
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
  const anchor = selectionAnchor, rangeAnchor = cellAnchor.value || currentCell.value;
  chooseCell(row, columns.value[column].name);
  if (event?.shiftKey && event.key !== 'Tab') {
    cellAnchor.value = rangeAnchor;
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
  if (command && event.key.toLowerCase() === 'c' && currentCell.value) { event.preventDefault(); void copySelection(); return; }
  if (event.key === 'Enter' && event.shiftKey) { event.preventDefault(); openCellEditor(cell.row, cell.column); return; }
  if (command && event.key.toLowerCase() === 'v') return;
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
  if (!canEdit(column) || saving.value) return;
  if (editing.value?.row === row && editing.value.column === column) return;
  chooseCell(row, column, undefined, isSelected(row));
  editing.value = { row, column, rows: props.rows }; draft.value = cellText(value,columns.value.find(item=>item.name===column)); error.value='';
  await nextTick(); input.value?.select();
}
async function persist(edits: GridEdit[]) {
  if (saving.value) return false;
  saving.value = true; error.value = '';
  try {
    if (props.saveRows) await props.saveRows(edits);
    else for (const edit of edits) emit('save', edit.rowIndex, edit.changes);
    return true;
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); return false; }
  finally { saving.value = false; }
}
async function commit(refocus = false) {
  const edit = editing.value;
  if (!edit || saving.value) return;
  if (edit.rows !== props.rows) { editing.value = undefined; return; }
  try {
    const column = columns.value.find(item => item.name === edit.column)!;
    const value = parseCellValue(draft.value, column);
    const original=props.rows[edit.row]?.[edit.column];
    const unchanged=value===original || (original!==null && value!==null && cellText(value)===cellText(original));
    if (!unchanged && !await persist([{rowIndex:edit.row, changes:{[edit.column]:value}}])) return;
    if (editing.value === edit) editing.value = undefined;
    if (refocus) void nextTick(focusGrid);
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
}
function cancelEdit() { editing.value = undefined; void nextTick(focusGrid); }
async function finishAndTab(event: KeyboardEvent) { await commit(); if (!editing.value && !tabCell(event.shiftKey)) void nextTick(focusGrid); }
function display(value: unknown, column?: (typeof columns.value)[number]) { return value===null?"NULL":cellText(value,column); }
function canEdit(name: string) { const column = columns.value.find(item=>item.name===name); return props.editable && column && !props.primaryKey.includes(name) && !isGenerated(column); }
const selection = computed(() => {
  if (rowSelection.value && selectedRows.value.size) return { rows:[...selectedRows.value].sort((a,b)=>a-b), columns:columns.value.map(item=>item.name) };
  const cell=rangeEnd.value||currentCell.value, anchor=cellAnchor.value || cell;
  if (!cell || !anchor) return {rows:[] as number[],columns:[] as string[]};
  const left=Math.min(columnIndexes.value.get(cell.column) ?? 0,columnIndexes.value.get(anchor.column) ?? 0),right=Math.max(columnIndexes.value.get(cell.column) ?? 0,columnIndexes.value.get(anchor.column) ?? 0);
  return {rows:Array.from({length:Math.abs(cell.row-anchor.row)+1},(_,i)=>Math.min(cell.row,anchor.row)+i),columns:columns.value.slice(left,right+1).map(item=>item.name)};
});
const rangeRows = computed(() => new Set(selection.value.rows));
const rangeColumns = computed(() => new Set(selection.value.columns));
function inSelection(row: number, column: string) { return rangeRows.value.has(row) && rangeColumns.value.has(column); }
async function copySelection() {
  try { const selected=selection.value; if (!selected.rows.length) return; await navigator.clipboard.writeText(delimitedText(selected.rows.map(index=>selected.columns.map(name=>props.rows[index]?.[name])))); error.value=''; }
  catch { error.value='无法访问剪贴板，请使用 Ctrl/⌘+C'; }
}
function openCellEditor(row=currentCell.value?.row, column=currentCell.value?.column) {
  if (row===undefined || !column) return;
  if(currentCell.value?.row!==row||currentCell.value?.column!==column)chooseCell(row,column);
  cellEditor.value={row,column,value:cellText(props.rows[row]?.[column],columns.value.find(item=>item.name===column)),nullValue:props.rows[row]?.[column]===null};error.value='';
}
const editorColumn = computed(() => columns.value.find(item=>item.name===cellEditor.value?.column));
async function saveCellEditor() {
  const editor=cellEditor.value,column=editorColumn.value;if(!editor||!column||!canEdit(column.name))return;
  try {const value=parseCellValue(editor.value,column,editor.nullValue);if(await persist([{rowIndex:editor.row,changes:{[column.name]:value}}]))cellEditor.value=undefined;}
  catch(reason){error.value=reason instanceof Error?reason.message:String(reason);}
}
function formatJson() { if(!cellEditor.value)return;try{cellEditor.value.value=formatCellJson(cellEditor.value.value);}catch{error.value='JSON 格式无效';} }
async function setNull() { const cell=currentCell.value,column=columns.value.find(item=>item.name===cell?.column);if(cell&&column&&canEdit(column.name)){if(column.nullable===false){error.value='该字段不允许 NULL';return;}await persist([{rowIndex:cell.row,changes:{[cell.column]:null}}]);} }
function preparePaste(text: string) {
  try {
    const cell=currentCell.value;if(!cell||!props.editable)throw new Error('请先选择一个可编辑单元格');
    const matrix=parseDelimited(text),start=columnIndexes.value.get(cell.column)??0;
    if(!matrix.length)throw new Error('剪贴板没有数据');
    if(matrix.length>1000||cell.row+matrix.length>props.rows.length)throw new Error('粘贴最多 1000 行，且不能超出当前已加载的记录');
    const edits=matrix.map((row,offset)=>{
      if(start+row.length>columns.value.length)throw new Error('粘贴内容超出当前表格的列范围');
      return {rowIndex:cell.row+offset,changes:Object.fromEntries(row.map((text,index)=>{const column=columns.value[start+index];if(!canEdit(column.name))throw new Error(`${column.name} 是主键、生成列或只读字段，请从可编辑列开始粘贴`);return [column.name,parsePastedValue(text,column)];}))};
    });
    pastePreview.value={text,edits,count:matrix.reduce((sum,row)=>sum+row.length,0)};error.value='';
  } catch(reason){error.value=reason instanceof Error?reason.message:String(reason);}
}
async function pasteFromClipboard() {try{preparePaste(await navigator.clipboard.readText());}catch{error.value='无法读取剪贴板，请选中单元格后按 Ctrl/⌘+V';} }
function paste(event: ClipboardEvent) {if((event.target as HTMLElement).closest('input,textarea,[contenteditable="true"]'))return;event.preventDefault();preparePaste(event.clipboardData?.getData('text/plain')||'');}
async function applyPaste() {if(pastePreview.value&&await persist(pastePreview.value.edits))pastePreview.value=undefined;}
const recordIndex = computed(() => currentCell.value?.row ?? 0);
watch(viewMode,mode=>{if(mode==='record'&&!currentCell.value&&props.rows.length){chooseCell(0,columns.value[0].name);rowSelection.value=true;}});
function moveRecord(delta:number) {if(!props.rows.length)return;const row=Math.max(0,Math.min(props.rows.length-1,recordIndex.value+delta));chooseCell(row,currentCell.value?.column||columns.value[0].name);rowSelection.value=true;}
const browseText = computed(() => viewMode.value==='json'?JSON.stringify(props.rows,null,2):viewMode.value==='text'?delimitedText([columns.value.map(item=>item.name),...props.rows.map(row=>columns.value.map(item=>row[item.name]))]):'');
function exportRows(format: string, selectedOnly=false) {const fields=selectedOnly?selection.value.columns:columns.value.map(item=>item.name);const rows=selectedOnly?selection.value.rows.map(index=>props.rows[index]):props.rows;if(!rows.length)return;emit('export',format,rows,fields);}
async function preparePrint() { viewMode.value='grid'; printing.value = true; await nextTick(); }
function finishPrint() { printing.value = false; }
defineExpose({ setWidth, autoFit, autoFitAll, setRowHeight, freeze, unfreeze, selectAll, deselectAll, goToRow, preparePrint, finishPrint, copySelection, pasteFromClipboard, editCurrentCell:openCellEditor, setNull });
</script>

<template>
  <div ref="frame" class="editable-grid-frame" role="grid" :aria-rowcount="rows.length + 1" :aria-colcount="columns.length + 1" :aria-activedescendant="activeDescendant" tabindex="0" :style="{ '--grid-row-height': `${rowHeight}px` }" @keydown="key" @paste="paste">
    <div class="grid-tools" aria-label="数据浏览与编辑">
      <div role="tablist" aria-label="数据浏览方式"><a-button v-for="item in ([['grid','表格'],['record','记录'],['text','文本'],['json','JSON']] as const)" :key="item[0]" type="text" role="tab" :aria-selected="viewMode===item[0]" @click="viewMode=item[0]">{{ item[1] }}</a-button></div>
      <span class="separator" /><a-button type="text" :disabled="!selection.rows.length" title="Shift 选择区域，Ctrl/⌘ 选择多行；Ctrl/⌘+C 复制" @click="copySelection">复制所选</a-button><a-button v-if="editable" type="text" :disabled="!currentCell || saving" @click="pasteFromClipboard">粘贴</a-button><a-button type="text" :disabled="!currentCell" @click="openCellEditor()">弹窗编辑</a-button>
      <a-dropdown @select="value=>{const [scope,format]=String(value).split(':');exportRows(format,scope==='selected')}"><a-button type="text">导出结果<Icon icon="lucide:chevron-down" /></a-button><template #content><a-doption v-for="format in ['csv','xlsx','sql','json']" :key="format" :value="`all:${format}`">导出已加载数据 · {{ format==='xlsx'?'Excel':format.toUpperCase() }}</a-doption><a-doption v-for="format in ['csv','xlsx','sql']" :key="`selected:${format}`" :value="`selected:${format}`" :disabled="!selection.rows.length">导出所选区域 · {{ format==='xlsx'?'Excel':format.toUpperCase() }}</a-doption></template></a-dropdown>
      <span class="grid-selection-summary">{{ selection.rows.length }} 行 × {{ selection.columns.length }} 列</span>
    </div>
    <div v-if="error && !cellEditor && !pastePreview" class="grid-error" role="alert">{{ error }}<a-button type="text" @click="error=''">关闭</a-button></div>
    <div v-if="viewMode==='record'" class="grid-record"><div class="grid-record-nav"><a-button :disabled="recordIndex===0" @click="moveRecord(-1)">上一条</a-button><span>第 {{ rows.length ? recordIndex+1 : 0 }} / {{ rows.length }} 条</span><a-button :disabled="recordIndex>=rows.length-1" @click="moveRecord(1)">下一条</a-button></div><dl><div v-for="column in columns" :key="column.name"><dt>{{ column.name }}<small>{{ columnType(column) }}</small></dt><dd><button type="button" :aria-label="`查看或编辑 ${column.name}`" @click="openCellEditor(recordIndex,column.name)">{{ display(rows[recordIndex]?.[column.name],column) }}</button></dd></div></dl></div>
    <textarea v-else-if="viewMode==='text'||viewMode==='json'" class="grid-text-view" :aria-label="viewMode==='json'?'JSON 数据浏览':'文本数据浏览'" :value="browseText" readonly />
    <div v-show="viewMode==='grid'" ref="shell" class="editable-grid-shell" @scroll.passive="scroll">
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
            <td v-for="(column, columnIndex) in columns" :id="cellId(rowIndex,column.name)" :key="column.name" :data-column="column.name" :aria-selected="isCurrent(rowIndex,column.name)" :class="{ null: row[column.name] === null, editable: editable && !primaryKey.includes(column.name), editing: editing?.row === rowIndex && editing?.column === column.name, frozen: columnIndex <= frozenThrough, 'active-cell': isCurrent(rowIndex,column.name), 'range-selected': inSelection(rowIndex,column.name) }" :style="stickyStyle(columnIndex)" :title="canEdit(column.name) ? '双击原地编辑' : display(row[column.name],column)" @click="chooseCell(rowIndex,column.name,$event)" @dblclick="edit(rowIndex,column.name,row[column.name])" @contextmenu.prevent="openMenu($event,rowIndex,column.name)">
              <WorkbenchInput v-if="editing?.row === rowIndex && editing?.column === column.name" :ref="setInput" v-model="draft" :aria-label="`编辑 ${column.name}`" @keydown.enter.stop.prevent="commit(true)" @keydown.esc.stop.prevent="cancelEdit" @keydown.tab.stop.prevent="finishAndTab" @blur="commit()" />
              <span v-else>{{ display(row[column.name],column) }}</span>
            </td>
          </tr>
          <tr v-if="bottomPadding" class="grid-spacer" aria-hidden="true"><td :colspan="columns.length + 1" :style="{ height: `${bottomPadding}px` }" /></tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty-grid"><Icon icon="lucide:database-zap" /><span>没有数据</span></div>
    </div>
    <WorkbenchModal v-if="cellEditor && editorColumn" visible :width="720" :footer="false" hide-title :closable="false" :mask-closable="false" @cancel="cellEditor=undefined">
      <section class="grid-value-dialog" role="dialog" aria-label="单元格编辑器">
        <header><strong>{{ canEdit(editorColumn.name)?'编辑':'查看' }} {{ editorColumn.name }}</strong><span>{{ columnType(editorColumn) }} · 第 {{ cellEditor.row+1 }} 条</span></header>
        <p v-if="error" role="alert" class="grid-error">{{ error }}</p>
        <WorkbenchCheckbox v-model="cellEditor.nullValue" :disabled="!canEdit(editorColumn.name)||editorColumn.nullable===false" aria-label="设为数据库 NULL">数据库 NULL</WorkbenchCheckbox>
        <CellValueInput v-model="cellEditor.value" :column="editorColumn" :label="`单元格值 ${editorColumn.name}`" :disabled="cellEditor.nullValue||!canEdit(editorColumn.name)||saving" />
        <p class="grid-value-hint">空字符串与 NULL 分开保存。大整数和精确小数保留完整精度。</p>
        <footer><a-button v-if="columnType(editorColumn).startsWith('json')" type="text" :disabled="cellEditor.nullValue" @click="formatJson">格式化 JSON</a-button><span class="grow" /><a-button @click="cellEditor=undefined">取消</a-button><a-button v-if="canEdit(editorColumn.name)" type="primary" :loading="saving" @click="saveCellEditor">保存单元格</a-button></footer>
      </section>
    </WorkbenchModal>
    <WorkbenchModal v-if="pastePreview" visible :width="760" :footer="false" hide-title :closable="false" :mask-closable="false" @cancel="pastePreview=undefined"><section class="grid-value-dialog" role="dialog" aria-label="确认粘贴数据"><header><strong>确认粘贴数据</strong><span>{{ pastePreview.edits.length }} 行 · {{ pastePreview.count }} 个单元格</span></header><p>将覆盖选中位置起的已有单元格。所有修改在同一事务中保存；失败时整体回滚。\N 表示 NULL，空白表示空字符串。</p><p v-if="error" role="alert" class="grid-error">{{ error }}</p><div class="grid-paste-preview"><table><thead><tr><th>记录</th><th>字段</th><th>原值</th><th>新值</th></tr></thead><tbody><template v-for="pasteEdit in pastePreview.edits.slice(0,30)" :key="pasteEdit.rowIndex"><tr v-for="(value,name) in pasteEdit.changes" :key="name"><td>{{ pasteEdit.rowIndex+1 }}</td><td>{{ name }}</td><td>{{ display(rows[pasteEdit.rowIndex]?.[name]) }}</td><td>{{ display(value) }}</td></tr></template></tbody></table></div><footer><span>预览前 30 行，共 {{ pastePreview.edits.length }} 行</span><span class="grow" /><a-button :disabled="saving" @click="pastePreview=undefined">取消</a-button><a-button type="primary" :loading="saving" @click="applyPaste">确认并保存</a-button></footer></section></WorkbenchModal>
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
.grid-tools { display:flex; flex:none; align-items:center; gap:2px; min-height:32px; padding:0 5px; border-bottom:1px solid #dce2eb; background:#f8fafc; overflow-x:auto; }
.grid-tools [role="tablist"] { display:flex; flex:none; }
.grid-tools :deep(.arco-btn) { flex:none; font-size:12px; padding:0 8px; height:28px; }
.grid-tools [aria-selected="true"] { color:#165dff; background:#e8f1ff; }
.grid-selection-summary { margin-left:auto; padding:0 8px; white-space:nowrap; color:#7c8b9b; font-size:12px; }
.grid-error { flex:none; padding:7px 12px; color:#c33c3c; background:#fff1f0; white-space:pre-wrap; }
.editable-grid-shell td.range-selected:not(.active-cell) { background:#eaf2ff!important; box-shadow:inset 0 0 0 1px #c2d6f2; }
.grid-record,.grid-text-view { flex:1; min-height:0; overflow:auto; padding:12px; }
.grid-record-nav { display:flex; gap:14px; align-items:center; }
.grid-record dl>div { display:grid; grid-template-columns:220px minmax(0,1fr); border-bottom:1px solid #e3e8ef; }
.grid-record dt { padding:10px; font-weight:600; }.grid-record dt small { display:block; color:#8b96a5; font-weight:400; }.grid-record dd { margin:0; min-width:0; }
.grid-record dd button { width:100%; padding:10px; border:0; text-align:left; background:transparent; color:inherit; white-space:pre-wrap; overflow-wrap:anywhere; cursor:pointer; }
.grid-text-view { width:100%; resize:none; border:0; outline:0; font:13px/1.6 ui-monospace,monospace; white-space:pre; }
.grid-value-dialog { padding:20px; }.grid-value-dialog header { display:flex; gap:12px; align-items:center; margin-bottom:16px; }.grid-value-dialog header span,.grid-value-hint { color:#8793a3; font-size:12px; }.grid-value-dialog footer { display:flex; gap:8px; align-items:center; margin-top:18px; }.grid-value-dialog :deep(.arco-checkbox) { margin-bottom:12px; }
.grid-paste-preview { max-height:360px; overflow:auto; }.grid-paste-preview table { width:100%; border-collapse:collapse; font-size:12px; }.grid-paste-preview th,.grid-paste-preview td { padding:6px; border:1px solid #e2e7ef; white-space:pre-wrap; overflow-wrap:anywhere; max-width:240px; }
@media print {
  .grid-tools,.grid-error { display:none; }
  .editable-grid-frame { display:block; height:auto; overflow:visible; border:0; }
  .grid-header { display:table-header-group; position:static; }
  .grid-header th { position:static!important; }
  .editable-grid-shell { height:auto; overflow:visible!important; }
  .editable-grid-shell td.active-cell { box-shadow:none!important; }
}
</style>
