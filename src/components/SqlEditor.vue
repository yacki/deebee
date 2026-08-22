<script setup lang="ts">
import type * as Monaco from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import { format } from "sql-formatter";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { Catalog } from "../types";

self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
const props = defineProps<{ modelValue: string; catalog?: Catalog; dialect?: "mysql" | "postgresql" }>();
const emit = defineEmits<{ "update:modelValue": [value: string]; run: [sql: string] }>();
const host = ref<HTMLElement>();
let monaco: typeof Monaco;
let instance: Monaco.editor.IStandaloneCodeEditor | undefined;
let completion: Monaco.IDisposable | undefined;
let updating = false;

const keywords = ["SELECT","FROM","WHERE","JOIN","LEFT JOIN","RIGHT JOIN","INNER JOIN","ON","AS","DISTINCT","INSERT INTO","VALUES","UPDATE","SET","DELETE FROM","CREATE TABLE","ALTER TABLE","DROP TABLE","GROUP BY","ORDER BY","HAVING","LIMIT","OFFSET","UNION ALL","WITH","CASE","WHEN","THEN","ELSE","END","AND","OR","NOT","NULL","IS NULL","IS NOT NULL","IN","LIKE","BETWEEN","EXISTS","ASC","DESC","COUNT","SUM","AVG","MIN","MAX","NOW","DATE_FORMAT","CONCAT","COALESCE","IFNULL","JSON_EXTRACT"];
const postgresKeywords = ["RETURNING","ILIKE","ON CONFLICT","DO NOTHING","DO UPDATE","GENERATED ALWAYS AS IDENTITY","SERIAL","BIGSERIAL","JSONB_BUILD_OBJECT","ARRAY_AGG","FILTER","LATERAL"];

function currentStatement(editor: Monaco.editor.IStandaloneCodeEditor) {
  const model = editor.getModel(); if (!model) return editor.getValue();
  const selection = editor.getSelection(); if (selection && !selection.isEmpty()) return model.getValueInRange(selection);
  const position = editor.getPosition(); const sql = editor.getValue(); const offset = position ? model.getOffsetAt(position) : 0;
  let start = offset; let end = offset;
  while (start > 0 && sql[start - 1] !== ";") start--;
  while (end < sql.length && sql[end] !== ";") end++;
  return sql.slice(start, end + (sql[end] === ";" ? 1 : 0)).trim() || sql;
}

function registerCompletion() {
  completion?.dispose();
  completion = monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "`", "\""],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range: Monaco.IRange = { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber, startColumn: word.startColumn, endColumn: word.endColumn };
      const before = model.getValueInRange({ startLineNumber: 1, startColumn: 1, endLineNumber: position.lineNumber, endColumn: position.column });
      const result: Monaco.languages.CompletionItem[] = [];
      const aliasMatch = before.match(/([A-Za-z_][\w$]*)\.([\w$]*)$/);
      const aliases = new Map<string,string>();
      for (const match of before.matchAll(/(?:FROM|JOIN|UPDATE|INTO)\s+`?([\w$]+)`?(?:\s+(?:AS\s+)?`?([\w$]+)`?)?/gi)) {
        aliases.set(match[1].toLowerCase(), match[1]);
        if (match[2] && !keywords.includes(match[2].toUpperCase())) aliases.set(match[2].toLowerCase(), match[1]);
      }
      if (aliasMatch && props.catalog) {
        const tableName = aliases.get(aliasMatch[1].toLowerCase()) || aliasMatch[1];
        const table = props.catalog.tables.find(item => item.name.toLowerCase() === tableName.toLowerCase());
        if (table) return { suggestions: table.columns.map(column => ({
          label: column.name, kind: monaco.languages.CompletionItemKind.Field, detail: `${table.name} · ${column.data_type}`,
          documentation: column.nullable ? "允许 NULL" : "NOT NULL", insertText: column.name,
          range: { ...range, startColumn: range.endColumn - aliasMatch[2].length }, sortText: `0_${column.name}`,
        })) };
      }
      const tableContext = /(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+`?[\w$]*$/i.test(before);
      if (props.catalog) {
        for (const table of props.catalog.tables) result.push({ label: table.name, kind: table.object_type === "VIEW" ? monaco.languages.CompletionItemKind.Interface : monaco.languages.CompletionItemKind.Struct, detail: `${props.catalog.database}${props.catalog.schema?`.`+props.catalog.schema:""} · ${table.object_type} · ${table.columns.length} 个字段`, insertText: table.name, range, sortText: tableContext ? `0_${table.name}` : `2_${table.name}` });
        if (!tableContext) {
          for (const table of props.catalog.tables) for (const column of table.columns) result.push({ label: column.name, kind: monaco.languages.CompletionItemKind.Field, detail: `${table.name} · ${column.data_type}`, insertText: column.name, range, sortText: `1_${column.name}` });
          for (const routine of props.catalog.routines) result.push({ label: routine.name, kind: monaco.languages.CompletionItemKind.Function, detail: `${routine.object_type} · ${routine.data_type}`, insertText: `${routine.name}($0)`, insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, range, sortText: `1_${routine.name}` });
        }
      }
      if (!tableContext) for (const keyword of props.dialect === "postgresql" ? [...keywords, ...postgresKeywords] : keywords) result.push({ label: keyword, kind: monaco.languages.CompletionItemKind.Keyword, detail: `${props.dialect === "postgresql" ? "PostgreSQL" : "MySQL"} 关键字`, insertText: keyword, range, sortText: `3_${keyword}` });
      return { suggestions: result };
    },
  });
}

function formatSql() { if (!instance) return; try { instance.setValue(format(instance.getValue(), { language: props.dialect === "postgresql" ? "postgresql" : "mysql", keywordCase: "upper" })); } catch { /* incomplete statement */ } }
defineExpose({ formatSql, focus: () => instance?.focus() });

onMounted(async () => {
  monaco = await import("monaco-editor/editor/editor.api.js");
  await Promise.all([
    import("monaco-editor/languages/definitions/sql/register.js"),
    import("monaco-editor/editor/contrib/suggest/browser/suggestController.js"),
    import("monaco-editor/editor/contrib/snippet/browser/snippetController2.js"),
    import("monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js"),
    import("monaco-editor/editor/contrib/folding/browser/folding.js"),
    import("monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js"),
    import("monaco-editor/editor/contrib/clipboard/browser/clipboard.js"),
    import("monaco-editor/editor/contrib/find/browser/findController.js"),
  ]);
  monaco.editor.defineTheme("deebee-light", { base: "vs", inherit: true, rules: [{ token: "keyword.sql", foreground: "0067C5", fontStyle: "bold" }, { token: "string.sql", foreground: "A04B18" }, { token: "comment.sql", foreground: "7B8793", fontStyle: "italic" }], colors: { "editor.lineHighlightBackground": "#F7FAFD", "editor.selectionBackground": "#B9DDF8", "editorCursor.foreground": "#1378D1" } });
  instance = monaco.editor.create(host.value!, { value: props.modelValue, language: "sql", theme: "deebee-light", automaticLayout: true, minimap: { enabled: false }, fontSize: 13, lineHeight: 22, suggestFontSize: 13, suggestLineHeight: 30, fontFamily: "SFMono-Regular, Menlo, Consolas, monospace", scrollBeyondLastLine: false, wordWrap: "off", folding: true, glyphMargin: false, lineNumbersMinChars: 3, suggestOnTriggerCharacters: true, suggestSelection: "first", suggest: { showIcons: true, showStatusBar: true, preview: true }, quickSuggestions: { other: true, comments: false, strings: false }, tabCompletion: "on", padding: { top: 10, bottom: 10 }, fixedOverflowWidgets: true, editContext: false });
  instance.onDidChangeModelContent(() => { if (!updating) emit("update:modelValue", instance!.getValue()); });
  instance.addAction({ id: "deebee-run", label: "运行 SQL", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter], run: () => emit("run", currentStatement(instance!)) });
  instance.addAction({ id: "deebee-format", label: "格式化 SQL", keybindings: [monaco.KeyMod.Shift | monaco.KeyMod.Alt | monaco.KeyCode.KeyF], run: formatSql });
  registerCompletion();
});
watch(() => props.modelValue, value => { if (instance && value !== instance.getValue()) { updating = true; instance.setValue(value); updating = false; } });
watch(() => props.catalog, registerCompletion, { deep: false });
onBeforeUnmount(() => { completion?.dispose(); instance?.dispose(); });
</script>

<template><div ref="host" class="sql-editor" data-testid="sql-editor" /></template>
