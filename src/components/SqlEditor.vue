<script setup lang="ts">
import type * as Monaco from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import { format } from "sql-formatter";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { sqlCompletions } from "../sqlCompletion";
import type { Catalog } from "../types";
import { aiRequest, type AIAccess, type AIStatus } from '../ai';
import { isNaturalLanguage, type PreparedRun, type RunMeta } from '../aiRun';

self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
const props = defineProps<{ modelValue: string; catalog?: Catalog; dialect?: "mysql" | "postgresql" | "mssql"; language?: 'shell'; aiAccess?: AIAccess; contextId?: string; runDisabled?: boolean; draftOnly?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: string]; run: [sql: string, meta?: RunMeta]; preparing: [value: boolean] }>();
const host = ref<HTMLElement>();
let monaco: typeof Monaco;
let instance: Monaco.editor.IStandaloneCodeEditor | undefined;
let completion: Monaco.IDisposable | undefined;
let updating = false;
let disposed = false;
const aiStatus = ref<AIStatus>({ enabled: false, share_schema: false });
const aiPaused = ref(false), aiBusy = ref(false), aiMessage = ref('');
let inlineCompletion: Monaco.IDisposable | undefined;
let aiController: AbortController | undefined;
let aiGeneration = 0, lastStatusCheck = 0, retryAfter = 0;
const aiPath = () => props.aiAccess ? '/v1/ai' : '/ai';
const generationOpen = ref(false), instruction = ref(''), generating = ref(false), generationError = ref('');
const instructionInput = ref<HTMLTextAreaElement>();
const codeLabel = computed(() => props.language === 'shell' ? '命令' : 'SQL');
const shortcutLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ I' : 'Ctrl I';
let generationController: AbortController | undefined;
let conversionRange: Monaco.Range | undefined;
let conversionVersion = 0, conversionGeneration = 0;

const preparing = ref(false), runError = ref(''), runNotice = ref(''), clarification = ref('');
const prepared = ref<PreparedRun>();
const previewExpanded = ref(false), awaitingConfirmation = ref(false);
const runKey = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ Enter' : 'Ctrl Enter';
let runController: AbortController | undefined;
let runGeneration = 0, runVersion = 0, runSource = '';
function cancelRun(clear = true) {
  runGeneration++; runController?.abort(); preparing.value = false; emit('preparing', false);
  if (clear) { prepared.value = undefined; runError.value = ''; runNotice.value = ''; clarification.value = ''; awaitingConfirmation.value = false; }
}
function dispatchPrepared() {
  if (!prepared.value?.code || runVersion !== instance?.getModel()?.getVersionId() || props.runDisabled || props.draftOnly) return;
  const result = prepared.value;
  awaitingConfirmation.value = false;
  runNotice.value = result.auto_run ? '正在查询…' : '正在提交…';
  emit('run', result.code, { generated: true, readOnly: result.auto_run });
}
async function prepareRun(source: string, request = source) {
  const model = instance?.getModel();
  if (!model || preparing.value || props.runDisabled) return;
  cancelRun(); cancelAI(); closeGeneration(false);
  const generation = runGeneration, version = model.getVersionId();
  runVersion = version; runSource = source;
  const controller = new AbortController(); runController = controller;
  const timeout = window.setTimeout(() => controller.abort(), 35000);
  preparing.value = true; emit('preparing', true); runNotice.value = '正在理解并生成…';
  instance?.trigger('ai', 'editor.action.inlineSuggest.hide', {});
  try {
    await refreshAI();
    if (!aiStatus.value.enabled) throw new Error('AI 尚未启用，请在管理端配置后重试。');
    const result = await aiRequest<PreparedRun>(aiPath() + '/prepare', {
      access: props.aiAccess, signal: controller.signal,
      body: { instruction: request.replace(/^[?？]\s*/, '').trim(), language: props.language || props.dialect || 'mysql', schema_context: schemaContext(), resource_id: props.aiAccess?.resourceId || '' },
    });
    if (disposed || controller.signal.aborted || generation !== runGeneration || model !== instance?.getModel() || version !== model.getVersionId()) return;
    prepared.value = result; clarification.value = '';
    if (!result.code) { runNotice.value = ''; runError.value = result.message || '请补充操作对象和条件。'; return; }
    // Only the server's conservative parser can opt a query into auto-run.
    const autoRun = result.auto_run === true && result.requires_confirmation === false && props.language !== 'shell';
    prepared.value = { ...result, auto_run: autoRun };
    previewExpanded.value = !autoRun;
    awaitingConfirmation.value = !autoRun;
    if (props.draftOnly) { runNotice.value = '命令已生成，复制到终端前请检查'; return; }
    if (autoRun) dispatchPrepared();
    else runNotice.value = props.language === 'shell' ? '请检查命令，确认后提交' : '请检查此 SQL 的操作内容，确认后运行';
  } catch (e) {
    if (!disposed && generation === runGeneration) { runNotice.value = ''; runError.value = controller.signal.aborted ? '生成超时，原始需求已保留，请重试。' : e instanceof Error ? e.message : '生成失败，请重试。'; }
  } finally {
    window.clearTimeout(timeout);
    if (generation === runGeneration) { preparing.value = false; emit('preparing', false); }
  }
}
function run(all = false) {
  if (!instance || preparing.value || props.runDisabled) return;
  if (awaitingConfirmation.value && !props.draftOnly) { dispatchPrepared(); return; }
  const value = all ? instance.getValue() : currentStatement(instance);
  if (!value.trim()) return;
  if (isNaturalLanguage(value, props.language === 'shell')) void prepareRun(value);
  else if (!props.draftOnly) { cancelRun(); emit('run', value); }
}
function continueRun() {
  if (clarification.value.trim()) void prepareRun(runSource, `${runSource}\n补充：${clarification.value.trim()}`);
}
async function copyPrepared() {
  try { await navigator.clipboard.writeText(prepared.value?.code || ''); runNotice.value = '已复制，可粘贴到终端'; }
  catch { runError.value = '复制失败，请选择命令后复制。'; }
}
function editPrepared() {
  if (!instance || !prepared.value?.code) return;
  const code = prepared.value.code; cancelRun();
  instance.pushUndoStop(); instance.executeEdits('deebee-prepared-edit', [{ range: instance.getModel()!.getFullModelRange(), text: code }]); instance.pushUndoStop(); instance.focus();
}
watch(() => props.runDisabled, (busy, previous) => {
  if (!busy && previous && prepared.value?.code && !awaitingConfirmation.value) runNotice.value = '查询已结束，可展开查看实际 SQL';
});

function schemaContext() {
  return aiStatus.value.share_schema && props.catalog ? props.catalog.tables.slice(0, 50).map(t => `${t.name}(${t.columns.slice(0, 60).map(c => c.name).join(', ')})`).join('\n').slice(0, 8000) : '';
}
function closeGeneration(focus = true) {
  conversionGeneration++; generationController?.abort(); generating.value = false;
  generationOpen.value = false; generationError.value = ''; conversionRange = undefined;
  if (focus) instance?.focus();
}
async function openGeneration() {
  const model = instance?.getModel(), selection = instance?.getSelection();
  if (!model || !selection) return;
  closeGeneration(false); cancelAI(); cancelRun();
  instance?.trigger('ai', 'editor.action.inlineSuggest.hide', {});
  instance?.trigger('ai', 'hideSuggestWidget', {});
  conversionRange = selection.isEmpty() ? new monaco.Range(selection.positionLineNumber, 1, selection.positionLineNumber, model.getLineMaxColumn(selection.positionLineNumber)) : selection;
  conversionVersion = model.getVersionId();
  instruction.value = model.getValueInRange(conversionRange).trim();
  generationError.value = aiStatus.value.enabled ? '' : 'AI 尚未启用，请在管理端配置后刷新页面。';
  generationOpen.value = true;
  await nextTick(); instructionInput.value?.focus(); instructionInput.value?.select();
}
function promptKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void generateCode(); }
}
async function generateCode() {
  const model = instance?.getModel(), range = conversionRange;
  if (!model || !range || !instruction.value.trim() || generating.value || !aiStatus.value.enabled) return;
  if (model.getVersionId() !== conversionVersion) { generationError.value = '编辑器内容已变化，请关闭此栏后重新选择要转换的内容。'; return; }
  const generation = ++conversionGeneration;
  const controller = new AbortController(); generationController = controller;
  const timeout = window.setTimeout(() => controller.abort(), 35000);
  generating.value = true; generationError.value = ''; aiMessage.value = '';
  try {
    const result = await aiRequest<{ code: string; message: string }>(aiPath() + '/generate', {
      access: props.aiAccess, signal: controller.signal,
      body: { instruction: instruction.value.trim(), language: props.language || props.dialect || 'mysql', schema_context: schemaContext(), resource_id: props.aiAccess?.resourceId || '' },
    });
    if (disposed || generation !== conversionGeneration) return;
    if (model !== instance?.getModel() || model.getVersionId() !== conversionVersion) { generationError.value = '编辑器内容已变化，生成结果未覆盖你的输入。请关闭此栏后重新生成。'; return; }
    if (!result.code) { generationError.value = result.message || '请补充具体对象和条件后重试。'; return; }
    // One undo restores the original natural-language text. Generation never runs it.
    instance.pushUndoStop();
    instance.executeEdits('deebee-natural-language', [{ range, text: result.code, forceMoveMarkers: true }]);
    instance.pushUndoStop();
    closeGeneration();
    aiMessage.value = `已生成${codeLabel.value}，可继续编辑或撤销`;
  } catch (e) {
    if (!disposed && generation === conversionGeneration) generationError.value = controller.signal.aborted ? '生成超时，原文已保留，请重试。' : e instanceof Error ? e.message : '生成失败，原文已保留。';
  } finally {
    window.clearTimeout(timeout);
    if (generation === conversionGeneration) generating.value = false;
  }
}

function cancelAI() { aiGeneration++; aiController?.abort(); aiBusy.value = false; }
async function refreshAI() {
  if (Date.now() - lastStatusCheck < 15000) return;
  lastStatusCheck = Date.now();
  try {
    const status = await aiRequest<AIStatus>(aiPath() + '/status', { access: props.aiAccess });
    if (disposed) return;
    aiStatus.value = status;
    instance?.updateOptions({ inlineSuggest: { enabled: Boolean(status.enabled) && !aiPaused.value } });
    if (!status.enabled) { cancelAI(); instance?.trigger('ai', 'editor.action.inlineSuggest.hide', {}); }
  } catch { if (!disposed) { aiStatus.value.enabled = false; cancelAI(); instance?.updateOptions({ inlineSuggest: { enabled: false } }); } }
}
function toggleAI() {
  aiPaused.value = !aiPaused.value; cancelAI(); aiMessage.value = '';
  instance?.updateOptions({ inlineSuggest: { enabled: aiStatus.value.enabled && !aiPaused.value } });
  instance?.trigger('ai', 'editor.action.inlineSuggest.hide', {});
  instance?.focus();
}
function registerAI() {
  inlineCompletion = monaco.languages.registerInlineCompletionsProvider(props.language === 'shell' ? 'shell' : 'sql', {
    debounceDelayMs: 650,
    async provideInlineCompletions(model, position, _context, token) {
      if (model !== instance?.getModel() || !aiStatus.value.enabled || aiPaused.value || generationOpen.value || preparing.value || disposed || Date.now() < retryAfter || !instance.hasTextFocus()) return { items: [] };
      const offset = model.getOffsetAt(position), value = model.getValue();
      const prefix = value.slice(Math.max(0, offset - 12000), offset);
      if (prefix.trim().length < 3 || isNaturalLanguage(value, props.language === 'shell') || !instance.getSelection()?.isEmpty()) return { items: [] };
      cancelAI();
      const generation = aiGeneration, version = model.getVersionId();
      const controller = new AbortController(); aiController = controller;
      const cancellation = token.onCancellationRequested(() => controller.abort());
      const timeout = window.setTimeout(() => controller.abort(), 35000);
      aiBusy.value = true; aiMessage.value = '';
      try {
        if (token.isCancellationRequested) return { items: [] };
        const schema = schemaContext();
        const result = await aiRequest<{ completion: string; reason: string }>(aiPath() + '/completions', {
          access: props.aiAccess, signal: controller.signal,
          body: { prefix, suffix: value.slice(offset, offset + 4000), language: props.language || props.dialect || 'mysql', schema_context: schema, resource_id: props.aiAccess?.resourceId || '' },
        });
        if (disposed || controller.signal.aborted || token.isCancellationRequested || generation !== aiGeneration || model.getVersionId() !== version || !instance?.getPosition()?.equals(position)) return { items: [] };
        if (result.reason === 'sensitive') aiMessage.value = '包含凭据，已跳过 AI 续写';
        if (result.reason === 'disabled') { aiStatus.value.enabled = false; instance.updateOptions({ inlineSuggest: { enabled: false } }); }
        return { items: result.completion ? [{ insertText: result.completion, range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column) }] : [] };
      } catch (e) {
        if (!disposed && generation === aiGeneration && !token.isCancellationRequested) {
          aiMessage.value = controller.signal.aborted ? 'AI 响应超时，可继续输入' : e instanceof Error ? e.message : 'AI 暂不可用';
          retryAfter = Date.now() + 15000;
        }
        return { items: [] };
      } finally {
        cancellation.dispose(); window.clearTimeout(timeout);
        if (generation === aiGeneration) aiBusy.value = false;
      }
    },
    disposeInlineCompletions() {},
  });
}

function currentStatement(editor: Monaco.editor.IStandaloneCodeEditor) {
  const model = editor.getModel(); if (!model) return editor.getValue();
  const selection = editor.getSelection(); if (selection && !selection.isEmpty()) return model.getValueInRange(selection);
  const position = editor.getPosition(); const sql = editor.getValue(); const offset = position ? model.getOffsetAt(position) : 0;
  let start = 0; let state: "normal"|"single"|"double"|"backtick"|"line"|"block"|"dollar" = "normal"; let dollar = "";
  for (let index = 0; index < sql.length; index++) {
    const char = sql[index]; const next = sql[index + 1];
    if (state === "line") { if (char === "\n") state = "normal"; continue; }
    if (state === "block") { if (char === "*" && next === "/") { state = "normal"; index++; } continue; }
    if (state === "dollar") { if (sql.startsWith(dollar, index)) { state = "normal"; index += dollar.length - 1; } continue; }
    if (state !== "normal") {
      const quote = state === "single" ? "'" : state === "double" ? '"' : "`";
      if (char === "\\") { index++; continue; }
      if (char === quote && next === quote) { index++; continue; }
      if (char === quote) state = "normal";
      continue;
    }
    if ((char === "-" && next === "-") || char === "#") { state = "line"; if (next === "-") index++; continue; }
    if (char === "/" && next === "*") { state = "block"; index++; continue; }
    if (char === "'") { state = "single"; continue; }
    if (char === '"') { state = "double"; continue; }
    if (char === "`") { state = "backtick"; continue; }
    if (char === "$") { const match = sql.slice(index).match(/^\$(?:[A-Za-z_][\w$]*)?\$/); if (match) { dollar = match[0]; state = "dollar"; index += dollar.length - 1; continue; } }
    if (char === ";") {
      const end = index + 1;
      if (offset >= start && offset <= end) return sql.slice(start, end).trim() || sql;
      start = end;
    }
  }
  return sql.slice(start).trim() || sql;
}

function registerCompletion() {
  if (disposed || !instance || props.language === 'shell') return;
  completion?.dispose();
  completion = monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "`", "\"", "["],
    provideCompletionItems(model, position) {
      // Providers are global to the SQL language; each editor owns one model.
      if (model !== instance?.getModel()) return { suggestions: [] };
      const { context, suggestions } = sqlCompletions(model.getValue(), model.getOffsetAt(position), props.catalog, props.dialect);
      const start = model.getPositionAt(context.replaceStart);
      const end = model.getPositionAt(context.replaceEnd);
      const range = { startLineNumber: start.lineNumber, startColumn: start.column, endLineNumber: end.lineNumber, endColumn: end.column };
      const kinds = {
        keyword: monaco.languages.CompletionItemKind.Keyword,
        table: monaco.languages.CompletionItemKind.Struct,
        view: monaco.languages.CompletionItemKind.Interface,
        column: monaco.languages.CompletionItemKind.Field,
        function: monaco.languages.CompletionItemKind.Function,
      };
      return {
        incomplete: true,
        suggestions: suggestions.map(item => ({
          label: { label: item.label, description: item.detail }, kind: kinds[item.kind],
          insertText: item.insertText, filterText: item.filterText, sortText: item.sortText, range,
          insertTextRules: item.snippet ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet : undefined,
        })),
      };
    },
  });
}

function formatSql() { if (!instance || props.language === 'shell') return; try { instance.setValue(format(instance.getValue(), { language: props.dialect === "postgresql" ? "postgresql" : props.dialect === "mssql" ? "transactsql" : "mysql", keywordCase: "upper" })); } catch { /* incomplete statement */ } }
defineExpose({ formatSql, run, cancelRun, focus: () => instance?.focus() });

onMounted(async () => {
  monaco = await import("monaco-editor/editor/editor.api.js");
  // Language registration initializes Monaco's standalone service container.
  // Load contributions first so production chunk timing cannot initialize it
  // before the suggestion memory service has registered.
  await Promise.all([
    import("monaco-editor/editor/contrib/suggest/browser/suggestController.js"),
    import("monaco-editor/editor/contrib/snippet/browser/snippetController2.js"),
    import("monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js"),
    import("monaco-editor/editor/contrib/folding/browser/folding.js"),
    import("monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js"),
    import("monaco-editor/editor/contrib/clipboard/browser/clipboard.js"),
    import("monaco-editor/editor/contrib/find/browser/findController.js"),
    import("monaco-editor/editor/contrib/inlineCompletions/browser/inlineCompletions.contribution.js"),
  ]);
  await import("monaco-editor/languages/definitions/sql/register.js");
  if (props.language === 'shell') monaco.languages.register({ id: 'shell' });
  if (disposed || !host.value) return;
  monaco.editor.defineTheme("deebee-light", { base: "vs", inherit: true, rules: [{ token: "keyword.sql", foreground: "0067C5", fontStyle: "bold" }, { token: "string.sql", foreground: "A04B18" }, { token: "comment.sql", foreground: "7B8793", fontStyle: "italic" }], colors: { "editor.lineHighlightBackground": "#F7FAFD", "editor.selectionBackground": "#B9DDF8", "editorCursor.foreground": "#1378D1" } });
  instance = monaco.editor.create(host.value!, { value: props.modelValue, language: "sql", theme: "deebee-light", automaticLayout: true, minimap: { enabled: false }, fontSize: 13, lineHeight: 22, suggestFontSize: 13, suggestLineHeight: 30, fontFamily: "SFMono-Regular, Menlo, Consolas, monospace", scrollBeyondLastLine: false, wordWrap: "off", folding: true, glyphMargin: false, lineNumbersMinChars: 3, suggestOnTriggerCharacters: true, suggestSelection: "first", wordBasedSuggestions: "off", suggest: { showIcons: true, showStatusBar: false, showInlineDetails: true, preview: false }, quickSuggestions: { other: true, comments: false, strings: false }, tabCompletion: "on", padding: { top: 10, bottom: 10 }, fixedOverflowWidgets: true, editContext: false });
  instance.onDidChangeModelContent(() => { cancelRun(); if (!updating) emit("update:modelValue", instance!.getValue()); });
  instance.addAction({ id: "deebee-run", label: "运行 SQL", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter], run: () => run() });
  instance.addAction({ id: "deebee-format", label: "格式化 SQL", keybindings: [monaco.KeyMod.Shift | monaco.KeyMod.Alt | monaco.KeyCode.KeyF], run: formatSql });
  instance.addAction({ id: 'deebee-generate', label: '自然语言转 SQL / 命令', keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI], run: openGeneration });
  registerCompletion();
  if (props.language === 'shell') monaco.editor.setModelLanguage(instance.getModel()!, 'shell');
  instance.updateOptions({ inlineSuggest: { enabled: false, showToolbar: 'onHover', suppressSuggestions: false }, ariaLabel: props.language === 'shell' ? '服务器命令' : 'SQL' });
  registerAI();
  // Monaco normally gives the suggestion list priority. When AI text is visible,
  // keep the advertised single-key accept/dismiss behavior for both widgets.
  instance.addCommand(monaco.KeyCode.Tab, () => {
    instance?.trigger('ai', 'editor.action.inlineSuggest.commit', {});
    instance?.trigger('ai', 'hideSuggestWidget', {});
  }, 'inlineSuggestionVisible && editorTextFocus && !editorReadonly');
  instance.addCommand(monaco.KeyCode.Escape, () => {
    cancelAI();
    instance?.trigger('ai', 'editor.action.inlineSuggest.hide', {});
    instance?.trigger('ai', 'hideSuggestWidget', {});
  }, 'inlineSuggestionVisible && editorTextFocus');
  instance.onDidFocusEditorText(() => { void refreshAI(); });
  instance.onDidBlurEditorText(cancelAI);
  void refreshAI();
});
watch(() => props.modelValue, value => { if (instance && value !== instance.getValue()) { updating = true; instance.setValue(value); updating = false; } });
watch(() => props.catalog, registerCompletion, { deep: false });
watch([() => props.contextId, () => props.aiAccess?.resourceId, () => props.aiAccess?.csrf, () => props.dialect, () => props.catalog, () => props.language], () => {
  cancelAI(); closeGeneration(false); cancelRun(); aiMessage.value = ''; retryAfter = 0;
  instance?.trigger('ai', 'editor.action.inlineSuggest.hide', {});
});
onBeforeUnmount(() => { disposed = true; cancelAI(); closeGeneration(false); cancelRun(); inlineCompletion?.dispose(); completion?.dispose(); instance?.dispose(); });
</script>

<template>
  <div class="ai-editor">
    <form v-if="generationOpen" class="ai-generation" aria-label="自然语言生成" @submit.prevent="generateCode" @keydown.esc.prevent.stop="closeGeneration()">
      <div class="ai-generation-controls">
        <textarea ref="instructionInput" v-model="instruction" rows="2" aria-label="自然语言需求" :maxlength="4000" :disabled="generating" :placeholder="language==='shell'?'例如：列出当前目录中大于 100MB 的文件':'例如：查询 users 表中 id 大于 100 的前 10 条记录'" @keydown="promptKeydown" />
        <button class="ai-generate-submit" type="submit" :disabled="generating || !instruction.trim() || !aiStatus.enabled">{{ generating ? '正在生成…' : `生成${codeLabel}` }}</button>
        <button type="button" @click="closeGeneration()">取消</button>
      </div>
      <p v-if="generationError" class="ai-generation-error" role="alert">{{ generationError }}</p>
      <p v-else>生成后替换选中文字或当前行，按撤销可恢复原文。</p>
    </form>
    <div ref="host" class="sql-editor" data-testid="sql-editor" />
    <section v-if="preparing || prepared || runError" class="ai-run-panel" aria-label="智能运行" :aria-busy="preparing">
      <header>
        <span role="status">{{ runNotice }}</span>
        <button v-if="prepared?.code" type="button" :aria-expanded="previewExpanded" @click="previewExpanded=!previewExpanded">{{ previewExpanded ? '收起' : '查看' }}本次{{ codeLabel }}</button>
        <button v-if="prepared?.code" type="button" @click="editPrepared">放入编辑器</button>
        <button v-if="draftOnly && prepared?.code" type="button" @click="copyPrepared">复制命令</button>
        <button v-else-if="awaitingConfirmation && prepared?.code" class="ai-run-confirm" type="button" :disabled="runDisabled" @click="dispatchPrepared">确认并运行</button>
        <button type="button" @click="cancelRun()">{{ preparing ? '取消生成' : '关闭' }}</button>
      </header>
      <pre v-if="previewExpanded && prepared?.code" tabindex="0"><code>{{ prepared.code }}</code></pre>
      <p v-if="runError" role="alert">{{ runError }}</p>
      <form v-if="prepared && !prepared.code" aria-label="补充需求" @submit.prevent="continueRun">
        <input v-model="clarification" aria-label="补充需求" maxlength="2000" placeholder="直接补充说明，按 Enter 继续" />
        <button type="submit" :disabled="!clarification.trim()">继续</button>
      </form>
    </section>

    <div v-if="aiStatus.enabled" class="ai-editor-status" role="status">
      <button class="ai-natural-button" type="button" :title="`转换选中文字或当前行（${shortcutLabel}）`" @click="openGeneration">自然语言转{{ codeLabel }}</button>
      <button type="button" :aria-pressed="!aiPaused" :title="'输入内容将发送到管理员配置的模型服务；点击暂停或恢复'" @click="toggleAI">✦ AI {{ aiPaused ? '已暂停' : aiBusy ? '续写中…' : '续写' }}</button>
      <span v-if="!aiMessage" class="ai-run-hint">{{ draftOnly ? '写下需求，按' : 'SQL 或自然语言，按' }} {{ runKey }} {{ draftOnly ? '生成命令' : '运行' }}</span><span>{{ aiMessage || (aiPaused ? '本编辑器已暂停' : 'Tab 接受 · Esc 忽略') }}</span>
    </div>
  </div>
</template>

<style scoped>
.ai-run-panel{flex:none;border-top:1px solid #dce2f1;background:#f4f6ff;padding:8px 10px;font-size:12px;max-height:45%;overflow:auto}.ai-run-panel header{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.ai-run-panel header>span{flex:1;min-width:100px;color:#475577}.ai-run-panel button{border:0;background:transparent;color:#4553c0;font:inherit;cursor:pointer;padding:4px}.ai-run-panel .ai-run-confirm{background:#4759c6;color:#fff;padding:6px 12px;border-radius:4px}.ai-run-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0;background:#fff;border:1px solid #dce2f1;padding:10px;max-height:150px;overflow:auto;color:#24324b}.ai-run-panel p{color:#a63131;margin:6px 0}.ai-run-panel form{display:flex;gap:8px}.ai-run-panel input{flex:1;min-width:0;padding:8px;border:1px solid #bac5dc;border-radius:4px;font:inherit}.ai-editor-status .ai-run-hint{color:#596b85}

.ai-editor{height:100%;width:100%;display:flex;flex-direction:column;min-height:0}.ai-editor .sql-editor{width:100%;height:0;flex:1;min-height:0}.ai-editor-status{flex:none;min-height:32px;display:flex;align-items:center;gap:14px;padding:0 10px;background:#f7f9ff;border-top:1px solid #e8edf6;color:#52627b;font-size:12px}.ai-editor-status button{border:0;background:transparent;color:#4553c0;cursor:pointer;padding:5px 0;font:inherit;white-space:nowrap}.ai-editor-status .ai-natural-button{font-weight:600}.ai-editor-status span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ai-generation{flex:none;padding:10px;background:#f4f6ff;border-bottom:1px solid #dce2f1;font-size:13px}.ai-generation-controls{display:flex;gap:8px;align-items:center}.ai-generation textarea{flex:1;min-width:0;border:1px solid #bac5dc;border-radius:5px;background:#fff;padding:8px 10px;color:#24324b;font:inherit;line-height:1.5;resize:vertical;max-height:120px}.ai-generation textarea:focus{outline:2px solid #9cacec;outline-offset:1px}.ai-generation button{flex:none;border:1px solid #c3cce0;border-radius:5px;background:#fff;color:#3e4e6b;padding:8px 12px;font:inherit;cursor:pointer}.ai-generation .ai-generate-submit{background:#4759c6;color:#fff;border-color:#4759c6}.ai-generation button:disabled{opacity:.55;cursor:default}.ai-generation p{margin:8px 0 0;font-size:12px;line-height:1.5;color:#596b85}.ai-generation .ai-generation-error{color:#a63131}@media(max-width:600px){.ai-generation-controls{flex-wrap:wrap}.ai-generation textarea{flex-basis:100%}.ai-editor-status{gap:9px;flex-wrap:wrap}.ai-editor-status span{flex-basis:100%;padding-bottom:4px}}
</style>
