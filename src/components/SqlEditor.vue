<script setup lang="ts">
import type * as Monaco from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import { format } from "sql-formatter";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { completionContext, currentSqlBeforeCursor, type SqlTableReference } from "../sqlAnalysis";
import type { Catalog } from "../types";

self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
const props = defineProps<{ modelValue: string; catalog?: Catalog; dialect?: "mysql" | "postgresql" | "mssql" }>();
const emit = defineEmits<{ "update:modelValue": [value: string]; run: [sql: string] }>();
const host = ref<HTMLElement>();
let monaco: typeof Monaco;
let instance: Monaco.editor.IStandaloneCodeEditor | undefined;
let completion: Monaco.IDisposable | undefined;
let updating = false;

const keywords = ["SELECT","FROM","WHERE","JOIN","LEFT JOIN","RIGHT JOIN","INNER JOIN","ON","AS","DISTINCT","INSERT INTO","VALUES","UPDATE","SET","DELETE FROM","CREATE TABLE","ALTER TABLE","DROP TABLE","GROUP BY","ORDER BY","HAVING","LIMIT","OFFSET","UNION ALL","WITH","CASE","WHEN","THEN","ELSE","END","AND","OR","NOT","NULL","IS NULL","IS NOT NULL","IN","LIKE","BETWEEN","EXISTS","ASC","DESC","COUNT","SUM","AVG","MIN","MAX","NOW","DATE_FORMAT","CONCAT","COALESCE","IFNULL","JSON_EXTRACT"];
const postgresKeywords = ["RETURNING","ILIKE","ON CONFLICT","DO NOTHING","DO UPDATE","GENERATED ALWAYS AS IDENTITY","SERIAL","BIGSERIAL","JSONB_BUILD_OBJECT","ARRAY_AGG","FILTER","LATERAL"];
const mssqlKeywords = ["TOP","OUTPUT","MERGE","IDENTITY","SCOPE_IDENTITY","TRY_CONVERT","TRY_CAST","NVARCHAR","DATETIME2","UNIQUEIDENTIFIER","CROSS APPLY","OUTER APPLY","SET NOCOUNT ON","BEGIN TRY","BEGIN CATCH","THROW"];

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
  completion?.dispose();
  completion = monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "`", "\""],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range: Monaco.IRange = { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber, startColumn: word.startColumn, endColumn: word.endColumn };
      const offset = model.getOffsetAt(position);
      const context = completionContext(currentSqlBeforeCursor(model.getValue(), offset));
      const result = new Map<string, Monaco.languages.CompletionItem>();
      const labels = new Set<string>();
      const add = (key: string, item: Monaco.languages.CompletionItem) => {
        const label = typeof item.label === "string" ? item.label.toLowerCase() : item.label.label.toLowerCase();
        if (!result.has(key) && !labels.has(label)) { result.set(key, item); labels.add(label); }
      };
      const catalog = props.catalog;
      const findTable = (name: string) => catalog?.tables.find(item => item.name.toLowerCase() === name.toLowerCase());
      const tableSuggestion = (table: NonNullable<typeof catalog>["tables"][number], order = "0") => ({
        label: table.name,
        kind: table.object_type === "VIEW" ? monaco.languages.CompletionItemKind.Interface : monaco.languages.CompletionItemKind.Struct,
        detail: `${catalog!.database}${catalog!.schema ? "." + catalog!.schema : ""} · ${table.object_type} · ${table.columns.length} 个字段`,
        insertText: table.name, range, sortText: `${order}_${table.name}`,
      });
      const columnSuggestion = (label: string, insertText: string, tableNames: string[], dataType: string, nullable: boolean, order = "0") => ({
        label, kind: monaco.languages.CompletionItemKind.Field,
        detail: `${tableNames.join(" / ")} · ${dataType}`,
        documentation: nullable ? "允许 NULL" : "NOT NULL", insertText, range, sortText: `${order}_${label}`,
      });

      if (catalog && context.qualifier) {
        const reference = context.references.find(item => [item.alias, item.table].some(name => name?.toLowerCase() === context.qualifier!.toLowerCase()));
        const table = findTable(reference?.table || context.qualifier);
        if (table) return { suggestions: table.columns.map(column => columnSuggestion(column.name, column.name, [table.name], column.data_type, column.nullable)) };
        const namespace = [catalog.database, catalog.schema].some(name => name?.toLowerCase() === context.qualifier!.toLowerCase());
        if (namespace) return { suggestions: catalog.tables.map(item => tableSuggestion(item)) };
      }
      if (catalog && context.tableContext) {
        for (const table of catalog.tables) add(`table:${table.name.toLowerCase()}`, tableSuggestion(table));
        return { suggestions: [...result.values()] };
      }
      if (catalog) {
        const scoped = context.references.flatMap(reference => {
          const table = findTable(reference.table);
          return table ? [{ reference, table }] : [];
        });
        const sources: { reference: SqlTableReference; table: Catalog["tables"][number] }[] = scoped.length ? scoped : catalog.tables.map(table => ({ reference: { table: table.name }, table }));
        const columns = new Map<string, { name: string; dataType: string; nullable: boolean; sources: typeof sources }>();
        for (const source of sources) for (const column of source.table.columns) {
          const key = column.name.toLowerCase(); const existing = columns.get(key);
          if (existing) existing.sources.push(source);
          else columns.set(key, { name: column.name, dataType: column.data_type, nullable: column.nullable, sources: [source] });
        }
        for (const column of columns.values()) {
          if (column.sources.length === 1) {
            const source = column.sources[0];
            add(`column:${column.name.toLowerCase()}`, columnSuggestion(column.name, column.name, [source.table.name], column.dataType, column.nullable));
          } else if (scoped.length) {
            for (const source of column.sources) {
              const prefix = source.reference.alias || source.table.name; const label = `${prefix}.${column.name}`;
              add(`column:${label.toLowerCase()}`, columnSuggestion(label, label, [source.table.name], column.dataType, column.nullable));
            }
          } else {
            add(`column:${column.name.toLowerCase()}`, columnSuggestion(column.name, column.name, column.sources.map(source => source.table.name), column.dataType, column.nullable, "1"));
          }
        }
        for (const routine of catalog.routines) add(`routine:${routine.name.toLowerCase()}`, { label: routine.name, kind: monaco.languages.CompletionItemKind.Function, detail: `${routine.object_type} · ${routine.data_type}`, insertText: `${routine.name}($0)`, insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, range, sortText: `2_${routine.name}` });
      }
      const dialectKeywords=props.dialect==="postgresql"?[...keywords,...postgresKeywords]:props.dialect==="mssql"?[...keywords,...mssqlKeywords]:keywords;
      for (const keyword of dialectKeywords) add(`keyword:${keyword}`, { label: keyword, kind: monaco.languages.CompletionItemKind.Keyword, detail: `${props.dialect === "postgresql" ? "PostgreSQL" : props.dialect === "mssql" ? "SQL Server" : "MySQL"} 关键字`, insertText: keyword, range, sortText: `3_${keyword}` });
      return { suggestions: [...result.values()] };
    },
  });
}

function formatSql() { if (!instance) return; try { instance.setValue(format(instance.getValue(), { language: props.dialect === "postgresql" ? "postgresql" : props.dialect === "mssql" ? "transactsql" : "mysql", keywordCase: "upper" })); } catch { /* incomplete statement */ } }
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
