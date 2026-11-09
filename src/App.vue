<script setup lang="ts">
import { Message } from "@arco-design/web-vue";
import { Icon } from "@iconify/vue";
import { confirmAction, promptValue } from "./components/ui/dialogs";
import BrandMark from "./components/BrandMark.vue";
import "./brand.css";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import ContextMenu from "./components/ContextMenu.vue";
import DatabaseTree from "./components/DatabaseTree.vue";
import EditableGrid from "./components/EditableGrid.vue";
import CellValueInput from "./components/CellValueInput.vue";
import { parseCellValue, isGenerated, type GridEdit } from "./cellValues";
import ObjectList from "./components/ObjectList.vue";
import RdpDesktop from "./components/RdpDesktop.vue";
import KubernetesWorkspace from "./components/KubernetesWorkspace.vue";
import SqlEditor from "./components/SqlEditor.vue";
import SshTerminal from "./components/SshTerminal.vue";
import SshFileManager from "./components/SshFileManager.vue";
import TableDesigner from "./components/TableDesigner.vue";
import { toolbarCapabilitiesFor, type ToolbarCapability } from "./toolbarPolicy";
import { API_BASE, ApiError, api, authToken, download, isNetworkError, isSessionExpiredError, jsonBody, workspaceId } from "./api";
import { inferEditableSingleSelectSource, inferSingleSelectSource, splitSqlStatements } from "./sqlAnalysis";
import type { Catalog, ConnectionDraft, ConnectionDriver, DataTab, Database, DatabaseSchema, DbObject, DesignerTab, MenuItem, MenuState, Objects, ObjectsTab, Profile, QueryResponse, QueryWireResponse, QueryTab, RemoteTab, ResultSet, TableData, TableSchema, TableSpec, WizardState, WorkTab } from "./types";

const TABLE_DATA_LIMIT = 1000;

type ConnectionTestResult = { latency_ms:number; version?:string; database?:string; host_key_fingerprint?:string; host_key_new?:boolean };
type ConnectionDialogState = ConnectionDraft & { mode: "create" | "edit"; id?: string; saving: boolean; testing: boolean; testMessage: string; error: string; nameFollowsHost: boolean; section: "general" | "options" | "ssh" | "proxy"; hostKey?: { fingerprint:string; host:string; port:number; signature:string; saveAfterConfirm:boolean } };
type QueryDraft = Pick<QueryTab, "title" | "database" | "schema" | "sql" | "savedSql">;
type QueryViewDraft = QueryDraft & Pick<QueryTab, "id" | "type">;
type DataViewDraft = Pick<DataTab, "id" | "type" | "title" | "database" | "schema" | "table" | "page" | "filterColumn" | "filterValue" | "sort">;
type DesignerViewDraft = Pick<DesignerTab, "id" | "type" | "title" | "database" | "schema" | "table" | "currentTable" | "pane" | "spec" | "source">;
type ObjectsViewDraft = Pick<ObjectsTab, "id" | "type" | "title" | "database" | "schema" | "kind">;
type WorkspaceViewDraft = QueryViewDraft | DataViewDraft | DesignerViewDraft | ObjectsViewDraft;
type ProfileWorkspace = { version: 1; activeTabId: string; tabs: WorkspaceViewDraft[] };
type ToolbarItem = { label: string; icon: string; action: () => void | Promise<void>; secondary?: boolean };
type BackgroundJob = { id:string;kind:string;status:"queued"|"running"|"cancelling"|"completed"|"failed"|"cancelled";progress:number;message:string;result?:unknown;error?:string;created_at:number;updated_at:number;metadata:Record<string,unknown> };

const user = ref(""); const authReady = ref(false); const loginUser = ref("admin"); const loginPassword = ref(""); const loginError = ref(""); const loginLoading = ref(false);
const loginPasswordVisible = ref(false);
const profiles = ref<Profile[]>([]); const profile = ref<Profile>(); const serverExpanded = ref(true); const databases = ref<Database[]>([]); const database = ref(""); const schema = ref(""); const schemas = ref<Record<string, DatabaseSchema[]>>({}); const expanded = ref(new Set<string>()); const expandedSchemas = ref(new Set<string>()); const objects = ref<Record<string, Objects>>({}); const catalogs = ref<Record<string, Catalog>>({}); const treeFilter = ref("");
const selectedTreeProfileId = ref("");
watch(() => profile.value?.id, id => { selectedTreeProfileId.value = id || ""; });
const tabs = ref<WorkTab[]>([]); const activeId = ref(""); const fatal = ref(""); const contextMenu = ref<MenuState | null>(null); const inspector = ref<{ title: string; subtitle: string; lines: string[] }>();
const resultPane = ref<string>("result-0"); const historyOpen = ref(false); const history = ref<string[]>([]); const savedQueries = ref<{ name:string; database:string; schema?:string; sql:string }[]>([]); const editorRef = ref<{ formatSql: () => void; focus: () => void }>();
const explorerMaxWidth = ref(Math.max(220,Math.min(600,window.innerWidth-520)));
const explorerWidth = ref(276);
try { const saved=Number(localStorage.getItem("deebee_explorer_width"));if(saved>=220)explorerWidth.value=Math.min(explorerMaxWidth.value,saved); } catch { /* Default width remains usable if storage is unavailable. */ }
function setExplorerWidth(value: string | number) {
  const width=Number.parseFloat(String(value));if(!Number.isFinite(width))return;
  explorerWidth.value=Math.round(Math.max(220,Math.min(explorerMaxWidth.value,width)));
  localStorage.setItem("deebee_explorer_width",String(explorerWidth.value));
}
// Arco measures size at the middle of the 6px divider.
function setExplorerSize(value: string | number) { setExplorerWidth(Number.parseFloat(String(value))-3); }
function resizeExplorer() { explorerMaxWidth.value=Math.max(220,Math.min(600,window.innerWidth-520));setExplorerWidth(explorerWidth.value); }
const queryViewRef = ref<HTMLElement>(); const queryEditorHeight = ref(260); let stopEditorResize: (() => void) | undefined;
const resultGridRef = ref<{ setWidth: (column: string, width: number) => void; autoFit: (column: string) => void; autoFitAll: () => void; setRowHeight: (height: number) => void; freeze: (column: string) => void; unfreeze: () => void; selectAll: () => void; deselectAll: () => void; goToRow: (row: number) => void; preparePrint: () => Promise<void>; finishPrint: () => void; copySelection: () => Promise<void>; pasteFromClipboard: () => Promise<void>; editCurrentCell: () => void; setNull: () => Promise<void> }>();
type InsertValueMode = "default" | "value" | "null";
const rowDialog = ref<{ mode: "insert"; tabId: string; values: Record<string, string>; valueModes: Record<string, InsertValueMode> }>(); const wizard = ref<WizardState>(); const wizardStep = ref(1); const wizardFormat = ref("csv"); const wizardCount = ref(100); const wizardIncludeData = ref(true); const wizardFile = ref<File>(); const wizardRunning = ref(false); const jobCancelling = ref(false); const jobProgress = ref({ value: 0, message: "" }); const activeJobId = ref("");
type ImportPreview = { source_columns: string[]; target_columns: TableSchema['columns']; rows: Record<string,unknown>[]; row_count: number };
const wizardPreview = ref<ImportPreview>();
const wizardMapping = ref<Record<string,string>>({});
const wizardDelimiter = ref(','); const wizardEncoding = ref('utf-8-sig'); const wizardNullMarker = ref('\\N');
const wizardColumns = ref<string[]>([]); const wizardDefinitions = ref<TableSchema['columns']>([]); const wizardIncludeStructure = ref(false);
const wizardError = ref(''); const wizardPreviewing = ref(false);
const wizardIsSql = computed(()=>wizard.value?.kind==='sql'||/\.sql$/i.test(wizardFile.value?.name||''));
const wizardHasMapping = computed(()=>wizard.value?.kind==='import'&&!wizardIsSql.value);
const wizardFinalStep = computed(()=>wizardHasMapping.value?3:2);
watch(wizardFile,()=>{wizardPreview.value=undefined;wizardError.value='';});
function setExportColumn(name:string,enabled:boolean){wizardColumns.value=wizardDefinitions.value.filter(column=>column.name===name?enabled:wizardColumns.value.includes(column.name)).map(column=>column.name);}
function importOptions(){return new URLSearchParams({delimiter:wizardDelimiter.value,encoding:wizardEncoding.value,null_marker:wizardNullMarker.value,mapping:JSON.stringify(wizardMapping.value)}).toString();}
async function wizardNext(){
  if(!wizard.value||!profile.value)return;wizardError.value='';
  if(wizardHasMapping.value&&wizardStep.value===1){
    if(!wizardFile.value||!wizard.value.table)return;wizardPreviewing.value=true;
    try{const target=wizard.value;const form=new FormData();form.append('file',wizardFile.value);const query=new URLSearchParams({profile_id:profile.value.id,database:target.database,schema:target.schema,table:target.table!,delimiter:wizardDelimiter.value,encoding:wizardEncoding.value,null_marker:wizardNullMarker.value});
      const preview=await api<ImportPreview>(`/data/import/preview?${query}`,{method:'POST',body:form});
      if(wizard.value!==target)return;wizardPreview.value=preview;wizardMapping.value=Object.fromEntries(preview.source_columns.map(name=>[name,preview.target_columns.some(column=>column.name===name)?name:'']));wizardStep.value=2;
    }catch(reason){wizardError.value=errorText(reason);}finally{wizardPreviewing.value=false;}
  }else if(wizardHasMapping.value&&wizardStep.value===2){const targets=Object.values(wizardMapping.value).filter(Boolean);if(!targets.length||new Set(targets).size!==targets.length){wizardError.value='请至少映射一个字段，同一目标字段不能重复映射';return;}wizardStep.value=3;}
  else wizardStep.value++;
}

const backgroundJobs = ref<BackgroundJob[]>([]); const jobsOpen = ref(false); let jobPollId = 0;
const searchDialog = ref<{ term: string; rows: { kind: string; name: string; detail: string }[] }>();
const createDatabaseDialog = ref<{ name: string; charset: string; collation: string; recovery_model: "SIMPLE" | "FULL" | "BULK_LOGGED"; data_size_mb: number; log_size_mb: number; filegrowth_mb: number; saving: boolean }>();
const connectionDialog = ref<ConnectionDialogState>();
const fileManagerProfile = ref<Profile>();
const connectionState = ref<"online" | "checking" | "offline">("checking"); const reconnecting = ref(false); let heartbeatId = 0;
let pauseDraftPersistence = false;
const hydratedProfileIds = new Set<string>();
const lastActiveTabIds = new Map<string,string>();
const uid = () => crypto.randomUUID().slice(0, 8);
const isPostgres = computed(() => profile.value?.driver === "postgresql");
const isMssql = computed(() => profile.value?.driver === "mssql");
const isSchemaDriver = computed(() => profile.value?.driver === "postgresql" || profile.value?.driver === "mssql");
const isRelational = computed(() => Boolean(profile.value && isRelationalDriver(profile.value.driver)));
const driverMetadata: Record<ConnectionDriver, { label: string; port: number; user: string; database: string; schema: string; icon: string }> = {
  mysql: { label:"MySQL", port:3306, user:"root", database:"", schema:"", icon:"lucide:database" },
  postgresql: { label:"PostgreSQL", port:5432, user:"postgres", database:"postgres", schema:"public", icon:"lucide:boxes" },
  mssql: { label:"SQL Server", port:1433, user:"sa", database:"master", schema:"dbo", icon:"lucide:database-zap" },
  redis: { label:"Redis", port:6379, user:"", database:"0", schema:"", icon:"lucide:blocks" },
  clickhouse: { label:"ClickHouse", port:8123, user:"default", database:"default", schema:"", icon:"lucide:columns-3" },
  mongodb: { label:"MongoDB", port:27017, user:"", database:"", schema:"", icon:"lucide:leaf" },
  ssh: { label:"SSH", port:22, user:"root", database:"", schema:"", icon:"lucide:square-terminal" },
  rdp: { label:"Remote Desktop", port:3389, user:"", database:"", schema:"", icon:"lucide:monitor-up" },
  k8s: { label:"Kubernetes", port:443, user:"", database:"", schema:"", icon:"lucide:ship-wheel" },
};
const driverChoices: ConnectionDriver[] = ["mysql", "postgresql", "mssql", "redis", "clickhouse", "mongodb", "ssh", "rdp", "k8s"];
function isRelationalDriver(driver: ConnectionDriver) { return driver === "mysql" || driver === "postgresql" || driver === "mssql"; }
function usesSchemas(driver: ConnectionDriver) { return driver === "postgresql" || driver === "mssql"; }
function isDirectRemoteDriver(driver: ConnectionDriver) { return driver === "ssh" || driver === "rdp"; }
function isRemoteDriver(driver: ConnectionDriver) { return driver === "ssh" || driver === "rdp" || driver === "k8s"; }
function isDatabaseDriver(driver: ConnectionDriver) { return !isRemoteDriver(driver); }
function isConnectionOnlyDriver(driver: ConnectionDriver) { return driver === "redis" || driver === "clickhouse" || driver === "mongodb"; }
function driverLabel(driver: ConnectionDriver) { return driverMetadata[driver].label; }
function contextKey(db: string, itemSchema = "") { return itemSchema ? `${db}:${itemSchema}` : db; }
function schemaKey(db: string, itemSchema: string) { return `${db}:${itemSchema}`; }
function schemaQuery(itemSchema: string) { return itemSchema ? `?schema=${encodeURIComponent(itemSchema)}` : ""; }
function defaultSchema(db: string) { const items = schemas.value[db] || []; const fallback=profile.value?.driver==="mssql"?"dbo":"public";return items.find(item => item.name === profile.value?.default_schema)?.name || items.find(item => item.name === fallback)?.name || items.find(item => !item.system)?.name || items[0]?.name || fallback; }
const connectionStringPreview = computed(() => {
  const item=connectionDialog.value;if(!item)return "";const user=item.user?encodeURIComponent(item.user):"user";const host=item.host||"host";const db=item.default_database?`/${encodeURIComponent(item.default_database)}`:"";
  const protocols:Record<ConnectionDriver,string>={mysql:"mysql",postgresql:"postgresql",mssql:"sqlserver",redis:"redis",clickhouse:"clickhouse",mongodb:"mongodb",ssh:"ssh",rdp:"rdp",k8s:"https"};
  return `${protocols[item.driver]}://${user}@${host}:${item.port}${db}`;
});
const active = computed(() => tabs.value.find(tab => tab.id === activeId.value));
const activeData = computed(() => active.value?.type === "data" ? active.value : undefined);
const activeDesigner = computed(() => active.value?.type === "designer" ? active.value : undefined);
const activeObjectsTab = computed(() => active.value?.type === "objects" ? active.value : undefined);
const sshTabs = computed(() => tabs.value.filter((item): item is RemoteTab => item.type === "ssh"));
const rdpTabs = computed(() => tabs.value.filter((item): item is RemoteTab => item.type === "rdp"));
const k8sTabs = computed(() => tabs.value.filter((item): item is RemoteTab => item.type === "k8s"));
const activeRemoteTabs = computed(() => tabs.value.filter((item): item is RemoteTab => (item.type === "ssh" || item.type === "rdp" || item.type === "k8s") && item.profileId === profile.value?.id));
const visibleProfiles = computed(() => {
  const query = treeFilter.value.trim().toLowerCase();
  if (!query) return profiles.value;
  return profiles.value.filter(item => item.id === profile.value?.id || item.name.toLowerCase().includes(query) || item.host.toLowerCase().includes(query) || driverLabel(item.driver).toLowerCase().includes(query));
});
const activeResult = computed<ResultSet | undefined>(() => { const tab = active.value; if (tab?.type !== "query") return; const index = Number(resultPane.value.replace("result-", "")); return tab.response?.results[index]; });
// Profile describes execution, not result data. Never traverse rows while switching panes.
const queryProfileText = computed(() => {
  const response = active.value?.type === "query" ? active.value.response : undefined;
  if (!response) return "尚未执行查询";
  return JSON.stringify({
    ok: response.ok, elapsed_ms: response.elapsed_ms, autocommit: response.autocommit,
    results: response.results.map(result => ({
      index: result.index, kind: result.kind, row_count: result.row_count,
      affected_rows: result.affected_rows, last_insert_id: result.last_insert_id,
      truncated: result.truncated, columns: result.columns, source: result.source,
    })),
    warnings: response.warnings || [],
  }, null, 2);
});
const queryStatusText = computed(() => JSON.stringify(active.value?.type === "query" ? active.value.response?.status || {} : {}, null, 2));
const connectionLabel = computed(() => connectionState.value === "online" ? "已连接" : connectionState.value === "checking" ? "正在重连" : "已断开");
const runningJobCount = computed(() => backgroundJobs.value.filter(item=>["queued","running","cancelling"].includes(item.status)).length);

onMounted(async () => {
  try { history.value = JSON.parse(localStorage.getItem("deebee_history") || "[]"); savedQueries.value = JSON.parse(localStorage.getItem("deebee_saved_queries") || "[]"); } catch { history.value = []; savedQueries.value = []; }
  const savedHeight = Number(localStorage.getItem("deebee_query_editor_height")); if (Number.isFinite(savedHeight) && savedHeight > 0) queryEditorHeight.value = savedHeight;
  if (authToken()) { user.value = "admin"; await bootstrap(); await refreshJobs(); }
  authReady.value = true;
  heartbeatId = window.setInterval(() => { if (!document.hidden) void checkConnection(false); }, 15000);
  jobPollId = window.setInterval(() => { if (user.value && (jobsOpen.value || runningJobCount.value)) void refreshJobs(); }, 1000);
  window.addEventListener("resize", resizeExplorer);
  window.addEventListener("online", reconnectAfterNetwork); window.addEventListener("offline", markOffline); window.addEventListener("beforeunload", beforeUnload); window.addEventListener("pagehide", releaseWorkspace); document.addEventListener("visibilitychange", reconnectWhenVisible);
});
onBeforeUnmount(() => { window.removeEventListener("resize",resizeExplorer);persistWorkspaces(); stopEditorResize?.(); window.clearInterval(heartbeatId); window.clearInterval(jobPollId); window.removeEventListener("online", reconnectAfterNetwork); window.removeEventListener("offline", markOffline); window.removeEventListener("beforeunload", beforeUnload); window.removeEventListener("pagehide", releaseWorkspace); document.removeEventListener("visibilitychange", reconnectWhenVisible); });

function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败"; }
function flash(message:string,error=false){if(error)Message.error({content:message,duration:4000});else Message.success({content:message,duration:2800});}
function flashError(reason: unknown) { if (isNetworkError(reason)) connectionState.value = "offline"; flash(`错误：${errorText(reason)}`, true); }
function draftKey(profileId: string) { return `deebee_query_drafts:${profileId}`; }
function workspaceKey(profileId: string) { return `deebee_workspace:${profileId}`; }
function isQueryTab(tab: WorkTab): tab is QueryTab { return tab.type === "query"; }
function isDatabaseTab(tab: WorkTab): tab is Exclude<WorkTab,RemoteTab> { return tab.type !== "ssh" && tab.type !== "rdp" && tab.type !== "k8s"; }
function markBackendOnline() {
  const tab = active.value;
  connectionState.value = tab && !isDatabaseTab(tab) ? (tab.state || "checking")
    : profile.value && isRemoteDriver(profile.value.driver) ? "offline" : "online";
}
function queryDirty(tab: WorkTab) { return tab.type === "query" && tab.sql !== tab.savedSql; }
function profileForTab(tab: WorkTab) { return profiles.value.find(item => item.id === tab.profileId); }
function profileNameForTab(tab: WorkTab) { return profileForTab(tab)?.name || "连接已删除"; }
function tabTitle(tab: WorkTab) {
  if (tab.type !== "ssh" && tab.type !== "rdp") return tab.title;
  const number = tab.sessionNumber || 1;
  return `${profileForTab(tab)?.name || tab.title}${number > 1 ? ` (${number})` : ""}`;
}
function isWorkspaceViewDraft(value: unknown): value is WorkspaceViewDraft {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<WorkspaceViewDraft>;
  return typeof item.id === "string" && typeof item.title === "string" && typeof item.database === "string" && typeof item.schema === "string" && ["query","data","designer","objects"].includes(String(item.type));
}
function readWorkspace(profileId: string): ProfileWorkspace | undefined {
  try {
    const saved = localStorage.getItem(workspaceKey(profileId));
    if (saved) {
      const value = JSON.parse(saved) as Partial<ProfileWorkspace>;
      if (value.version === 1 && Array.isArray(value.tabs)) return { version:1, activeTabId:typeof value.activeTabId === "string" ? value.activeTabId : "", tabs:value.tabs.filter(isWorkspaceViewDraft) };
    }
    const legacy = JSON.parse(localStorage.getItem(draftKey(profileId)) || "[]") as QueryDraft[];
    if (Array.isArray(legacy) && legacy.length) {
      const tabs: QueryViewDraft[] = legacy.filter(item=>item&&typeof item.sql==="string"&&typeof item.database==="string").map(item=>({ id:uid(), type:"query", title:item.title||"查询", database:item.database, schema:item.schema||"", sql:item.sql, savedSql:item.savedSql??item.sql }));
      return { version:1, activeTabId:tabs[0]?.id||"", tabs };
    }
  } catch { /* Ignore malformed local workspace state. */ }
}
function workspaceDraft(tab: Exclude<WorkTab,RemoteTab>): WorkspaceViewDraft {
  if (tab.type === "query") return { id:tab.id,type:tab.type,title:tab.title,database:tab.database,schema:tab.schema,sql:tab.sql,savedSql:tab.savedSql };
  if (tab.type === "data") return { id:tab.id,type:tab.type,title:tab.title,database:tab.database,schema:tab.schema,table:tab.table,page:tab.page,filterColumn:tab.filterColumn,filterValue:tab.filterValue,sort:tab.sort };
  if (tab.type === "designer") return { id:tab.id,type:tab.type,title:tab.title,database:tab.database,schema:tab.schema,table:tab.table,currentTable:tab.currentTable,pane:tab.pane,spec:tab.spec,source:tab.source };
  return { id:tab.id,type:tab.type,title:tab.title,database:tab.database,schema:tab.schema,kind:tab.kind };
}
function persistWorkspaces() {
  if (pauseDraftPersistence) return;
  const ids = new Set([...hydratedProfileIds,...tabs.value.filter(isDatabaseTab).map(tab=>tab.profileId)]);
  for (const profileId of ids) {
    const profileTabs = tabs.value.filter((tab):tab is Exclude<WorkTab,RemoteTab>=>isDatabaseTab(tab)&&tab.profileId===profileId);
    const remembered = lastActiveTabIds.get(profileId) || "";
    const value: ProfileWorkspace = { version:1, activeTabId:profileTabs.some(tab=>tab.id===remembered)?remembered:profileTabs[0]?.id||"", tabs:profileTabs.map(workspaceDraft) };
    localStorage.setItem(workspaceKey(profileId),JSON.stringify(value));
    localStorage.removeItem(draftKey(profileId));
  }
}
function beforeUnload(event: BeforeUnloadEvent) { persistWorkspaces(); if(tabs.value.some(queryDirty)||tabs.value.some(tab=>tab.type==="query"&&!tab.autocommit)){event.preventDefault();event.returnValue="";} }
function releaseWorkspace(event: PageTransitionEvent) {
  if(event.persisted||!authToken())return;
  void fetch(`${API_BASE}/sessions/cleanup`,{method:"POST",headers:{"Content-Type":"application/json",Authorization:`Bearer ${authToken()}`},body:jsonBody({workspace_id:workspaceId()}),keepalive:true}).catch(()=>undefined);
}
// Persist drafts, not transient query results. Traversing every result cell on
// tab switches or loading-state changes blocks the UI for large result sets.
watch([() => tabs.value.filter(isDatabaseTab).map(tab => ({ profileId: tab.profileId, ...workspaceDraft(tab) })), activeId], persistWorkspaces, { deep:true });
function updateTab(id: string, patch: Partial<WorkTab>) { const tab = tabs.value.find(item => item.id === id); if (tab) Object.assign(tab, patch); }
function closeMenu() { contextMenu.value = null; }
function markOffline() { if (user.value) connectionState.value = "offline"; }
function reconnectAfterNetwork() { void checkConnection(false); }
function reconnectWhenVisible() { if (!document.hidden) void checkConnection(false); }
function shouldRecreateSession(reason: unknown) {
  return isSessionExpiredError(reason) || (reason instanceof ApiError && (
    (typeof reason.code === "number" && [2006, 2013, 2055].includes(reason.code)) ||
    (typeof reason.code === "string" && /^08/.test(reason.code))
  ));
}
function safeToReplay(sql: string) {
  const statement = sql.replace(/^(?:\s|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/)+/, "");
  return /^(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|VALUES)\b/i.test(statement);
}
async function recreateQuerySession(tab: QueryTab) {
  if (!profileForTab(tab)) throw new Error("数据库连接已不存在");
  const previous = tab.sessionId;
  const session = await api<{ id: string }>("/sessions", { method: "POST", body: jsonBody({ profile_id: tab.profileId, database: tab.database, schema: tab.schema, autocommit: tab.autocommit, workspace_id: workspaceId() }) });
  tab.sessionId = session.id;
  if(previous)void api(`/sessions/${previous}`, { method: "DELETE" }).catch(() => undefined);
  return session.id;
}
async function ensureQuerySession(tab: QueryTab) {
  if (!tab.sessionId) { await recreateQuerySession(tab); return "rebuilt" as const; }
  try { await api(`/sessions/${tab.sessionId}`); return "ok" as const; }
  catch (reason) {
    if (!shouldRecreateSession(reason)) throw reason;
    const transactionLost = !tab.autocommit;
    await recreateQuerySession(tab);
    if (transactionLost) tab.error = "连接已恢复，但原手动事务已丢失，请核对数据并重新开始事务。";
    return transactionLost ? "transaction-lost" as const : "rebuilt" as const;
  }
}
async function checkConnection(showFeedback: boolean) {
  if (!user.value || reconnecting.value) return connectionState.value === "online";
  const previous = connectionState.value; reconnecting.value = true; if (showFeedback || previous === "offline") connectionState.value = "checking";
  try {
    await api("/health");
    if (profile.value && isConnectionOnlyDriver(profile.value.driver)) await api(`/connections/${profile.value.id}/test`, { method:"POST" });
    const states = await Promise.all(tabs.value.filter(isQueryTab).filter(tab => !tab.loading).map(ensureQuerySession));
    const rebuilt = states.filter(state => state !== "ok").length;
    const lostTransactions = states.filter(state => state === "transaction-lost").length;
    const remoteTab = active.value && !isDatabaseTab(active.value) ? active.value : undefined;
    markBackendOnline();
    if (profile.value && isRemoteDriver(profile.value.driver) && !rebuilt) {
      if (showFeedback) flash(connectionState.value === "online" ? "远程会话连接正常" : remoteTab ? "远程会话未连接，请在会话中重新连接" : "远程会话未连接，请先打开会话", connectionState.value !== "online");
      return connectionState.value === "online";
    }
    if (previous === "offline" || rebuilt) {
      for (const tab of tabs.value) if (tab.type === "query" && tab.error?.includes("连接中断")) tab.error = "连接已恢复，本条 SQL 未自动重放，请重新执行。";
      flash(lostTransactions ? `连接已恢复，但 ${lostTransactions} 个手动事务已丢失` : rebuilt ? `连接已恢复，已重建 ${rebuilt} 个查询会话` : "后台连接已恢复", lostTransactions > 0);
    }
    else if (showFeedback) flash("连接正常");
    return true;
  } catch (reason) {
    connectionState.value = "offline"; if (showFeedback) flashError(reason); return false;
  } finally { reconnecting.value = false; }
}
function editorHeightLimit() { return Math.max(160, (queryViewRef.value?.clientHeight || innerHeight) - 165); }
function setEditorHeight(value: number) { queryEditorHeight.value = Math.round(Math.min(editorHeightLimit(), Math.max(120, value))); localStorage.setItem("deebee_query_editor_height", String(queryEditorHeight.value)); }
function resizeEditorByKeyboard(event: KeyboardEvent) {
  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  if (event.key === "Home") setEditorHeight(120); else if (event.key === "End") setEditorHeight(editorHeightLimit()); else setEditorHeight(queryEditorHeight.value + (event.key === "ArrowDown" ? 18 : -18));
}
function startEditorResize(event: PointerEvent) {
  if (event.button !== 0) return;
  event.preventDefault(); const startY = event.clientY; const startHeight = queryEditorHeight.value;
  const move = (next: PointerEvent) => setEditorHeight(startHeight + next.clientY - startY);
  const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); document.body.classList.remove("resizing-query"); stopEditorResize = undefined; editorRef.value?.focus(); };
  stopEditorResize?.(); stopEditorResize = stop; document.body.classList.add("resizing-query"); window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop, { once: true });
}
function activateTab(tab: WorkTab) { activeId.value = tab.id; lastActiveTabIds.set(tab.profileId,tab.id); database.value = tab.database; schema.value = tab.schema; if (!isDatabaseTab(tab)) connectionState.value = tab.state || "checking"; if (tab.type === "query") resultPane.value = tab.response?.results.some(result => result.kind === "rows") ? `result-${tab.response.results.findIndex(result => result.kind === "rows")}` : "message"; }
async function selectTab(tab: WorkTab) {
  const targetProfile=profileForTab(tab);if(!targetProfile)return flash("该标签所属连接已不存在",true);
  if(targetProfile.id!==profile.value?.id){try{await initializeProfile(targetProfile,tab);markBackendOnline();}catch(reason){connectionState.value="offline";flashError(reason);}return;}
  activateTab(tab);
  try { await prepareDatabaseTab(tab); } catch(reason) { flashError(reason); }
}

async function login() {
  loginLoading.value = true; loginError.value = "";
  try { const result = await api<{ token: string; user: { username: string } }>("/auth/login", { method: "POST", body: jsonBody({ username: loginUser.value, password: loginPassword.value }) }); sessionStorage.setItem("deebee_token", result.token); user.value = result.user.username; await bootstrap(); await refreshJobs(); }
  catch (reason) { loginError.value = errorText(reason); } finally { loginLoading.value = false; }
}
async function logout(force = false) {
  const manual = tabs.value.filter((tab): tab is QueryTab => tab.type === "query" && !tab.autocommit).length;
  const dirty = tabs.value.filter(queryDirty).length;
  if (!force && (manual || dirty) && !await confirmAction(`退出将关闭 ${manual} 个手动事务；${dirty} 个未保存查询草稿会保留在本机。确定退出吗？`)) return;
  persistWorkspaces();
  try { await api("/sessions/cleanup", { method: "POST", body: jsonBody({ workspace_id: workspaceId() }) }); } catch { /* Logout must remain available while offline. */ }
  sessionStorage.removeItem("deebee_token"); user.value = ""; tabs.value = []; profiles.value = []; profile.value = undefined; backgroundJobs.value=[];jobsOpen.value=false;hydratedProfileIds.clear(); lastActiveTabIds.clear(); connectionState.value = "offline";
}
async function bootstrap() {
  try {
    profiles.value = await api<Profile[]>("/connections"); await api("/sessions/cleanup", { method: "POST", body: jsonBody({ workspace_id: workspaceId() }) });
    const savedProfile = localStorage.getItem("deebee_profile_id"); const initial = profiles.value.find(item => item.id === savedProfile) || profiles.value[0];
    if (!initial) { resetConnectionWorkspace(); openCreateConnection(); return; }
    try { await initializeProfile(initial,undefined,false); markBackendOnline(); }
    catch (reason) { connectionState.value = "offline"; flash(`连接 ${initial.name} 暂不可用：${errorText(reason)}`, true); }
    restoreInactiveWorkspaces(initial.id);
  } catch (reason) { if (errorText(reason).includes("登录")) await logout(true); else fatal.value = errorText(reason); }
}
function resetConnectionWorkspace() { profile.value=undefined;databases.value=[];database.value="";schema.value="";schemas.value={};objects.value={};catalogs.value={};tabs.value=[];activeId.value="";expanded.value=new Set();expandedSchemas.value=new Set();hydratedProfileIds.clear();lastActiveTabIds.clear();connectionState.value="offline"; }
let profileInitialization = 0;
async function initializeProfile(nextProfile: Profile, preferredTab?: WorkTab, openRemote = true) {
  const generation = ++profileInitialization;
  const current = () => generation === profileInitialization && profile.value?.id === nextProfile.id;
  persistWorkspaces();
  pauseDraftPersistence = true;
  try {
    if(active.value)lastActiveTabIds.set(active.value.profileId,active.value.id);
    profile.value = nextProfile; serverExpanded.value = true; activeId.value = ""; objects.value = {}; catalogs.value = {}; schemas.value = {}; expandedSchemas.value = new Set();
    localStorage.setItem("deebee_profile_id", nextProfile.id);
    databases.value = []; database.value = ""; schema.value = ""; expanded.value = new Set();
    const savedWorkspace = !hydratedProfileIds.has(nextProfile.id) ? readWorkspace(nextProfile.id) : undefined;
    const savedPreferred = savedWorkspace?.tabs.find(item=>item.id===savedWorkspace.activeTabId);
    const preferred=preferredTab?.profileId===nextProfile.id?preferredTab:tabs.value.find(item=>item.id===lastActiveTabIds.get(nextProfile.id)&&item.profileId===nextProfile.id)||tabs.value.find(item=>item.profileId===nextProfile.id);
    if (isRemoteDriver(nextProfile.driver)) {
      if (needsSshHostKey(nextProfile)) {
        openEditConnection(nextProfile);
        await testConnectionDialog(true);
        return;
      }
      const remoteTab=preferred&&(preferred.type==="ssh"||preferred.type==="rdp"||preferred.type==="k8s")?preferred:tabs.value.find((item):item is RemoteTab=>item.profileId===nextProfile.id&&item.type===nextProfile.driver);
      if(remoteTab)activateTab(remoteTab);
      else {
        // RDP authenticates in the real desktop session. An automatic probe
        // would log in a second time and could count against account limits.
        if (nextProfile.driver !== "rdp" && (openRemote || !isDirectRemoteDriver(nextProfile.driver))) await api(`/connections/${nextProfile.id}/test`, { method:"POST" });
        if (!current()) return;
        if(openRemote)openRemoteSession(nextProfile.driver,nextProfile.id);
      }
      return;
    }
    if (!isRelationalDriver(nextProfile.driver)) {
      await api(`/connections/${nextProfile.id}/test`, { method:"POST" });
      return;
    }
    const availableDatabases = await api<Database[]>(`/connections/${nextProfile.id}/databases`);
    if (!current()) return;
    databases.value = availableDatabases;
    const availableDatabaseNames=new Set(databases.value.map(item=>item.name));
    tabs.value=tabs.value.filter(item=>item.profileId!==nextProfile.id||!isDatabaseTab(item)||availableDatabaseNames.has(item.database));
    const initial = databases.value.find(item => item.name === (preferred?.database||savedPreferred?.database))?.name || databases.value.find(item => item.name === nextProfile.default_database)?.name || databases.value[0]?.name || "";
    database.value = initial; schema.value = ""; expanded.value = new Set(initial ? [initial] : []);
    if (!initial) return;
    if (usesSchemas(nextProfile.driver)) {
      await loadSchemas(initial);
      if (!current()) return;
      const rememberedSchema = (preferred?.database||savedPreferred?.database) === initial ? (preferred?.schema||savedPreferred?.schema||"") : "";
      schema.value = (schemas.value[initial] || []).some(item => item.name === rememberedSchema) ? rememberedSchema : defaultSchema(initial);
      expandedSchemas.value = new Set([schemaKey(initial,schema.value)]);
    }
    await loadObjects(initial, schema.value);
    if (!current()) return;
    let restoredPreferred: WorkTab | undefined;
    if(!hydratedProfileIds.has(nextProfile.id)){
      hydratedProfileIds.add(nextProfile.id);
      restoredPreferred = await restoreWorkspaceTabs(nextProfile,savedWorkspace,databases.value);
      if (!current()) return;
    }
    if (!tabs.value.some(item=>item.profileId===nextProfile.id)) await newQuery(initial, "SELECT 1 AS ready;", schema.value,undefined,nextProfile.id);
    if (!current()) return;
    const target=preferred&&tabs.value.includes(preferred)?preferred:restoredPreferred||tabs.value.find(item=>item.id===lastActiveTabIds.get(nextProfile.id)&&item.profileId===nextProfile.id)||tabs.value.find(item=>item.profileId===nextProfile.id);if(target){activateTab(target);await prepareDatabaseTab(target);}
  } finally {
    if (current()) {
      pauseDraftPersistence = false;
      persistWorkspaces();
    }
  }
}
function restoreInactiveWorkspaces(initialProfileId: string) {
  pauseDraftPersistence=true;
  try {
    for(const targetProfile of profiles.value.filter(item=>item.id!==initialProfileId&&isRelationalDriver(item.driver))){
      const workspace=readWorkspace(targetProfile.id);if(!workspace)continue;hydratedProfileIds.add(targetProfile.id);
      let remembered="";
      for(const draft of workspace.tabs){
        const tabId=tabs.value.some(item=>item.id===draft.id)?uid():draft.id;let restored:WorkTab|undefined;
        if(draft.type==="query"&&typeof draft.sql==="string")restored={id:tabId,profileId:targetProfile.id,type:"query",title:draft.title,database:draft.database,schema:usesSchemas(targetProfile.driver)?draft.schema:"",sessionId:"",sql:draft.sql,savedSql:typeof draft.savedSql==="string"?draft.savedSql:draft.sql,autocommit:true,loading:false,resultIndex:0};
        else if(draft.type==="data"&&typeof draft.table==="string"&&draft.table)restored=reactive<DataTab>({id:tabId,profileId:targetProfile.id,type:"data",title:draft.title||draft.table,database:draft.database,schema:usesSchemas(targetProfile.driver)?draft.schema:"",table:draft.table,loading:false,page:Number.isInteger(draft.page)&&draft.page>0?draft.page:1,filterColumn:draft.filterColumn||"",filterValue:draft.filterValue||"",sort:draft.sort,selected:null});
        else if(draft.type==="designer"&&typeof draft.table==="string"&&draft.table&&targetProfile.driver!=="mssql")restored=reactive<DesignerTab>({id:tabId,profileId:targetProfile.id,type:"designer",title:draft.title||`${draft.table} — 设计`,database:draft.database,schema:usesSchemas(targetProfile.driver)?draft.schema:"",table:draft.table,currentTable:typeof draft.currentTable==="string"?draft.currentTable:null,loading:false,pane:draft.pane||"columns",spec:draft.spec,source:draft.source,statements:[],dangerous:[]});
        else if(draft.type==="objects"&&["tables","views","routines","triggers","events"].includes(draft.kind))restored={id:tabId,profileId:targetProfile.id,type:"objects",title:draft.title,database:draft.database,schema:usesSchemas(targetProfile.driver)?draft.schema:"",kind:draft.kind};
        if(!restored)continue;tabs.value.push(restored);if(draft.id===workspace.activeTabId)remembered=restored.id;
      }
      if(remembered)lastActiveTabIds.set(targetProfile.id,remembered);
    }
  } finally {pauseDraftPersistence=false;persistWorkspaces();}
}
async function prepareDatabaseTab(tab: WorkTab) {
  if (!isDatabaseTab(tab)) return;
  if(tab.type==="query"&&!tab.sessionId)await recreateQuerySession(tab);
  else if(tab.type==="data"&&!tab.data&&!tab.loading)await loadData(tab);
  else if(tab.type==="designer"&&!tab.spec&&!tab.loading)await loadDesignerTab(tab);
  else if(tab.type==="objects"&&!objects.value[contextKey(tab.database,tab.schema)])await loadObjects(tab.database,tab.schema);
}
async function restoreWorkspaceTabs(nextProfile: Profile, workspace: ProfileWorkspace | undefined, availableDatabases: Database[]) {
  if (!workspace) return;
  const available = new Set(availableDatabases.map(item=>item.name));
  let restoredPreferred: WorkTab | undefined;
  for (const draft of workspace.tabs.filter(item=>available.has(item.database))) {
    let itemSchema = usesSchemas(nextProfile.driver) ? draft.schema : "";
    if (usesSchemas(nextProfile.driver)) {
      if (!schemas.value[draft.database]) await loadSchemas(draft.database);
      if (!(schemas.value[draft.database] || []).some(item=>item.name===itemSchema)) itemSchema=defaultSchema(draft.database);
    }
    const tabId = tabs.value.some(item=>item.id===draft.id) ? uid() : draft.id;
    let restored: WorkTab | undefined;
    if (draft.type === "query") {
      if (typeof draft.sql !== "string") continue;
      await newQuery(draft.database,draft.sql,itemSchema,{id:tabId,title:draft.title,savedSql:typeof draft.savedSql==="string"?draft.savedSql:draft.sql},nextProfile.id);
      restored = tabs.value.find(item=>item.id===tabId);
    } else if (draft.type === "data") {
      if (typeof draft.table !== "string" || !draft.table) continue;
      const dataTab=reactive<DataTab>({id:tabId,profileId:nextProfile.id,type:"data",title:draft.title||draft.table,database:draft.database,schema:itemSchema,table:draft.table,loading:true,page:Number.isInteger(draft.page)&&draft.page>0?draft.page:1,filterColumn:draft.filterColumn||"",filterValue:draft.filterValue||"",sort:draft.sort,selected:null});
      tabs.value.push(dataTab);await loadData(dataTab);restored=dataTab;
    } else if (draft.type === "designer" && nextProfile.driver !== "mssql") {
      if (typeof draft.table !== "string" || !draft.table) continue;
      const designerTab=reactive<DesignerTab>({id:tabId,profileId:nextProfile.id,type:"designer",title:draft.title||`${draft.table} — 设计`,database:draft.database,schema:itemSchema,table:draft.table,currentTable:typeof draft.currentTable==="string"?draft.currentTable:null,loading:!draft.spec,pane:draft.pane||"columns",spec:draft.spec,source:draft.source,statements:[],dangerous:[]});
      tabs.value.push(designerTab);if(!designerTab.spec)await loadDesignerTab(designerTab);restored=designerTab;
    } else if (draft.type === "objects" && ["tables","views","routines","triggers","events"].includes(draft.kind)) {
      const objectsTab:ObjectsTab={id:tabId,profileId:nextProfile.id,type:"objects",title:draft.title,database:draft.database,schema:itemSchema,kind:draft.kind};tabs.value.push(objectsTab);restored=objectsTab;
    }
    if (restored && draft.id===workspace.activeTabId) restoredPreferred=restored;
  }
  if(restoredPreferred)lastActiveTabIds.set(nextProfile.id,restoredPreferred.id);
  return restoredPreferred;
}
let profileSwitch: { id: string; promise: Promise<void> } | undefined;
async function switchProfile(profileId: string, openRemote = true) {
  if (profileSwitch?.id === profileId) return profileSwitch.promise;
  const next = profiles.value.find(item => item.id === profileId); if (!next || next.id === profile.value?.id) return;
  const request = {
    id: profileId,
    promise: initializeProfile(next, undefined, openRemote)
      .then(() => { markBackendOnline(); flash(`已切换到 ${next.name}`); })
      .catch(flashError),
  };
  profileSwitch = request;
  try { await request.promise; }
  finally { if (profileSwitch === request) profileSwitch = undefined; }
}
function selectProfileNode(item: Profile) {
  selectedTreeProfileId.value = item.id;
  // Keep the tree stationary between the two clicks of a double-click.
  // Switching here would remove the preceding database tree and move this row.
  if (isDirectRemoteDriver(item.driver)) return;
  if (item.id === profile.value?.id) { serverExpanded.value = !serverExpanded.value; return; }
  void switchProfile(item.id);
}
function connectProfileNodeByKeyboard(event: KeyboardEvent, item: Profile) {
  if (!isDirectRemoteDriver(item.driver)) return;
  event.preventDefault();
  void connectProfileNode(item);
}
async function connectProfileNode(item: Profile) {
  if (!isDirectRemoteDriver(item.driver)) return;
  await switchProfile(item.id, false);
  if (profile.value?.id !== item.id) return;
  if (connectionDialog.value?.id === item.id && needsSshHostKey(item)) return;
  openRemoteSession(item.driver, item.id);
}
function newConnectionState(driver: ConnectionDriver, source?: Profile): ConnectionDialogState {
  const defaults = driverMetadata[driver]; const sourceOptions = source?.options;const host=source?.host||"127.0.0.1";
  return { mode:"create", driver, name:host, nameFollowsHost:true, host, port:source?.port||defaults.port, user:source?.user??defaults.user, password:"", private_key:"", ssh_password:"", ssh_private_key:"", proxy_password:"", default_database:source?.default_database??defaults.database, default_schema:source?.default_schema??defaults.schema, options:{tls:sourceOptions?.tls??false,verify_tls:sourceOptions?.verify_tls??true,auth_database:sourceOptions?.auth_database||"admin",direct_connection:sourceOptions?.direct_connection??false,auth_method:sourceOptions?.auth_method||"password",strict_host_key:sourceOptions?.strict_host_key??true,host_key_fingerprint:sourceOptions?.host_key_fingerprint||"",domain:sourceOptions?.domain||"",security:sourceOptions?.security||"any",ignore_certificate:sourceOptions?.ignore_certificate??false,color_depth:sourceOptions?.color_depth||24,timezone:sourceOptions?.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone||"Etc/UTC",k8s_auth_method:sourceOptions?.k8s_auth_method||"token",namespace:sourceOptions?.namespace||"",encryption:sourceOptions?.encryption||"request",read_only:sourceOptions?.read_only??false,ssh_tunnel:sourceOptions?.ssh_tunnel??false,ssh_host:sourceOptions?.ssh_host||"",ssh_port:sourceOptions?.ssh_port||22,ssh_user:sourceOptions?.ssh_user||"",ssh_auth_method:sourceOptions?.ssh_auth_method||"password",ssh_host_key_mode:sourceOptions?.ssh_host_key_mode||"accept_new",ssh_host_key_fingerprint:sourceOptions?.ssh_host_key_fingerprint||"",ssh_keepalive_seconds:sourceOptions?.ssh_keepalive_seconds??30,proxy_enabled:sourceOptions?.proxy_enabled??false,proxy_type:sourceOptions?.proxy_type||"socks5",proxy_host:sourceOptions?.proxy_host||"",proxy_port:sourceOptions?.proxy_port||1080,proxy_user:sourceOptions?.proxy_user||"",proxy_dns:sourceOptions?.proxy_dns??true}, saving:false, testing:false, testMessage:"", error:"", section:"general" };
}
function openCreateConnection(source?: Profile) { connectionDialog.value = newConnectionState(source?.driver||"mysql",source); }
function openEditConnection(target=profile.value) { const item=target;if(!item)return;connectionDialog.value={...newConnectionState(item.driver,item),mode:"edit",id:item.id,name:item.name,nameFollowsHost:false,password:""}; }
function setConnectionDriver(driver:ConnectionDriver){const dialog=connectionDialog.value;if(!dialog||dialog.driver===driver)return;const defaults=driverMetadata[driver];dialog.driver=driver;dialog.port=defaults.port;dialog.user=defaults.user;dialog.default_database=defaults.database;dialog.default_schema=defaults.schema;dialog.private_key="";dialog.ssh_password="";dialog.ssh_private_key="";dialog.proxy_password="";dialog.options={tls:false,verify_tls:true,auth_database:"admin",direct_connection:false,auth_method:"password",strict_host_key:true,host_key_fingerprint:"",domain:"",security:"any",ignore_certificate:false,color_depth:24,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||"Etc/UTC",k8s_auth_method:"token",namespace:"",encryption:"request",read_only:false,ssh_tunnel:false,ssh_host:"",ssh_port:22,ssh_user:"",ssh_auth_method:"password",ssh_host_key_mode:"accept_new",ssh_host_key_fingerprint:"",ssh_keepalive_seconds:30,proxy_enabled:false,proxy_type:"socks5",proxy_host:"",proxy_port:1080,proxy_user:"",proxy_dns:true};dialog.section="general";dialog.testMessage="";dialog.error="";}
function useCustomConnectionName(){if(connectionDialog.value?.mode==="create")connectionDialog.value.nameFollowsHost=false;}
async function loadKubeconfigFile(event:Event){const file=(event.target as HTMLInputElement).files?.[0];const dialog=connectionDialog.value;if(!file||!dialog)return;const value=await file.text();dialog.private_key=value;dialog.options.k8s_auth_method="kubeconfig";let server="";try{const parsed=JSON.parse(value) as {clusters?:Array<{cluster?:{server?:string}}>};server=parsed.clusters?.[0]?.cluster?.server||"";}catch{server=value.match(/^\s*server:\s*["']?([^\s"']+)/m)?.[1]||"";}try{const url=new URL(server);dialog.host=url.hostname;dialog.port=Number(url.port)||(url.protocol==="http:"?80:443);if(dialog.nameFollowsHost){dialog.name=url.hostname;}}catch{/* 后端在测试时给出精确的 kubeconfig 错误 */}}
watch(()=>connectionDialog.value?.host,host=>{const dialog=connectionDialog.value;if(dialog?.mode==="create"&&(dialog.nameFollowsHost||!dialog.name.trim())){dialog.name=(host||"").trim();dialog.nameFollowsHost=true;}});
function connectionPayload(dialog:ConnectionDialogState){let options:Partial<ConnectionDraft["options"]>={};if(isConnectionOnlyDriver(dialog.driver))options={tls:dialog.options.tls,verify_tls:dialog.options.verify_tls,...(dialog.driver==="mongodb"?{auth_database:dialog.options.auth_database,direct_connection:dialog.options.direct_connection}:{})};else if(dialog.driver==="mssql")options={encryption:dialog.options.encryption,read_only:dialog.options.read_only};else if(dialog.driver==="ssh")options={auth_method:dialog.options.auth_method,strict_host_key:dialog.options.strict_host_key,host_key_fingerprint:dialog.options.host_key_fingerprint};else if(dialog.driver==="rdp")options={domain:dialog.options.domain,security:dialog.options.security,ignore_certificate:dialog.options.ignore_certificate,color_depth:dialog.options.color_depth,timezone:dialog.options.timezone};else if(dialog.driver==="k8s")options={k8s_auth_method:dialog.options.k8s_auth_method,namespace:dialog.options.namespace,verify_tls:dialog.options.verify_tls};if(isDatabaseDriver(dialog.driver))options={...options,ssh_tunnel:dialog.options.ssh_tunnel,ssh_host:dialog.options.ssh_host,ssh_port:Number(dialog.options.ssh_port),ssh_user:dialog.options.ssh_user,ssh_auth_method:dialog.options.ssh_auth_method,ssh_host_key_mode:dialog.options.ssh_host_key_mode,ssh_host_key_fingerprint:dialog.options.ssh_host_key_fingerprint,ssh_keepalive_seconds:Number(dialog.options.ssh_keepalive_seconds),proxy_enabled:dialog.options.proxy_enabled,proxy_type:dialog.options.proxy_type,proxy_host:dialog.options.proxy_host,proxy_port:Number(dialog.options.proxy_port),proxy_user:dialog.options.proxy_user,proxy_dns:dialog.options.proxy_dns};return{driver:dialog.driver,name:dialog.name,host:dialog.host,port:Number(dialog.port),user:dialog.user,password:dialog.mode==="edit"&&!dialog.password?null:dialog.password,private_key:dialog.mode==="edit"&&!dialog.private_key?null:dialog.private_key,ssh_password:dialog.mode==="edit"&&!dialog.ssh_password?null:dialog.ssh_password,ssh_private_key:dialog.mode==="edit"&&!dialog.ssh_private_key?null:dialog.ssh_private_key,proxy_password:dialog.mode==="edit"&&!dialog.proxy_password?null:dialog.proxy_password,default_database:isRemoteDriver(dialog.driver)?"":dialog.default_database,default_schema:usesSchemas(dialog.driver)?dialog.default_schema:"",options};}
function needsSshHostKey(item: Pick<ConnectionDraft, "driver" | "options">) {
  return item.driver === "ssh" && item.options.strict_host_key !== false && !item.options.host_key_fingerprint;
}
function needsConnectionHostKey(item: ConnectionDraft) {
  return needsSshHostKey(item) || (isDatabaseDriver(item.driver) && item.options.ssh_tunnel && item.options.ssh_host_key_mode !== "insecure" && !item.options.ssh_host_key_fingerprint);
}
watch(() => {
  const dialog = connectionDialog.value;
  return dialog ? { dialog, host:dialog.host, port:dialog.port, sshHost:dialog.options.ssh_host, sshPort:dialog.options.ssh_port } : undefined;
}, (next, previous) => {
  if (!next || !previous || next.dialog !== previous.dialog) return;
  if (next.dialog.driver === "ssh" && (next.host !== previous.host || next.port !== previous.port)) next.dialog.options.host_key_fingerprint = "";
  if (next.sshHost !== previous.sshHost || next.sshPort !== previous.sshPort) next.dialog.options.ssh_host_key_fingerprint = "";
}, { flush:"sync" });
watch(() => connectionDialog.value && jsonBody(connectionPayload(connectionDialog.value)), signature => {
  const dialog = connectionDialog.value;
  if (dialog?.hostKey && dialog.hostKey.signature !== signature) dialog.hostKey = undefined;
}, { flush:"sync" });
async function testConnectionDialog(saveAfterConfirm = false) {
  const dialog = connectionDialog.value;
  if (!dialog || dialog.testing || dialog.saving) return false;
  const signature = jsonBody(connectionPayload(dialog));
  dialog.testing = true; dialog.testMessage = ""; dialog.error = ""; dialog.hostKey = undefined;
  try {
    const result = await api<ConnectionTestResult>("/connections/test", { method:"POST", body:jsonBody({...connectionPayload(dialog), profile_id:dialog.mode === "edit" ? dialog.id : ""}) });
    if (connectionDialog.value !== dialog || jsonBody(connectionPayload(dialog)) !== signature) return false;
    if (result.host_key_new && result.host_key_fingerprint && (dialog.driver === "ssh" || dialog.options.ssh_tunnel)) {
      dialog.hostKey = { fingerprint:result.host_key_fingerprint, host:dialog.driver === "ssh" ? dialog.host : dialog.options.ssh_host, port:Number(dialog.driver === "ssh" ? dialog.port : dialog.options.ssh_port), signature, saveAfterConfirm };
      return false;
    }
    if (needsConnectionHostKey(dialog)) throw new Error("SSH 测试未返回主机密钥指纹，请重新测试连接");
    dialog.testMessage = `连接成功 · ${result.latency_ms} ms${result.database ? ` · ${result.database}` : ""}`;
    return true;
  } catch (reason) {
    if (connectionDialog.value === dialog && jsonBody(connectionPayload(dialog)) === signature) dialog.error = errorText(reason);
    return false;
  } finally { dialog.testing = false; }
}
async function confirmConnectionHostKey() {
  const dialog = connectionDialog.value; const hostKey = dialog?.hostKey;
  if (!dialog || !hostKey || hostKey.signature !== jsonBody(connectionPayload(dialog))) return;
  dialog.hostKey = undefined;
  if (dialog.driver === "ssh") dialog.options.host_key_fingerprint = hostKey.fingerprint;
  else dialog.options.ssh_host_key_fingerprint = hostKey.fingerprint;
  dialog.error = ""; dialog.testMessage = "已确认 SSH 主机密钥，保存连接后生效";
  if (hostKey.saveAfterConfirm) await saveConnection();
}
function rejectConnectionHostKey() {
  const dialog = connectionDialog.value; if (!dialog) return;
  dialog.hostKey = undefined; dialog.testMessage = ""; dialog.error = "已取消信任 SSH 主机密钥，未保存连接";
}
async function saveConnection() {
  const dialog = connectionDialog.value;
  if (!dialog || dialog.testing || dialog.saving || dialog.hostKey) return;
  if (needsConnectionHostKey(dialog)) {
    const tested = await testConnectionDialog(true);
    if (!tested || connectionDialog.value !== dialog || needsConnectionHostKey(dialog)) return;
  }
  dialog.saving = true; dialog.error = "";
  try {
    const saved = await api<Profile & {resource_sync?: {state:string;message?:string}}>(dialog.mode === "edit" ? `/connections/${dialog.id}` : "/connections", {method:dialog.mode === "edit" ? "PATCH" : "POST", body:jsonBody(connectionPayload(dialog))});
    profiles.value = await api<Profile[]>("/connections"); connectionDialog.value = undefined;
    const next = profiles.value.find(item => item.id === saved.id)!;
    try {
      const freshRemote = next.driver === "ssh" || next.driver === "rdp";
      await initializeProfile(next, undefined, !freshRemote);
      if (freshRemote) openRemoteSession(next.driver, next.id);
      if (!freshRemote) markBackendOnline();
      flash(saved.resource_sync?.state === "pending" ? (saved.resource_sync.message || "连接已保存，资源池同步待重试") : `连接 ${saved.name} 已保存并加入资源管理`, saved.resource_sync?.state === "pending");
    } catch (reason) { connectionState.value = "offline"; flash(`连接已保存，但当前无法连接：${errorText(reason)}`, true); }
  } catch (reason) { dialog.error = errorText(reason); }
  finally { dialog.saving = false; }
}
async function deleteConnection(target=profile.value){if(!target||!await confirmAction(`确定删除连接“${target.name}”吗？已打开的查询会话会同时关闭。`))return;try{const currentId=profile.value?.id;await api(`/connections/${target.id}`,{method:"DELETE"});tabs.value=tabs.value.filter(tab=>tab.profileId!==target.id);hydratedProfileIds.delete(target.id);lastActiveTabIds.delete(target.id);localStorage.removeItem(draftKey(target.id));localStorage.removeItem(workspaceKey(target.id));profiles.value=await api<Profile[]>("/connections");const next=profiles.value.find(item=>item.id===currentId)||profiles.value[0];if(next&&next.id!==profile.value?.id){try{await initializeProfile(next);markBackendOnline();}catch(reason){connectionState.value="offline";flash(`连接 ${next.name} 暂不可用：${errorText(reason)}`,true);}}else if(!next){resetConnectionWorkspace();openCreateConnection();}flash(`连接 ${target.name} 已删除`);}catch(reason){flashError(reason);}}
async function loadSchemas(db: string) {
  if (!profile.value || !usesSchemas(profile.value.driver)) return [];
  const payload = await api<DatabaseSchema[]>(`/connections/${profile.value.id}/databases/${encodeURIComponent(db)}/schemas`);
  schemas.value = { ...schemas.value, [db]: payload }; return payload;
}
async function loadObjects(db: string, itemSchema = "") {
  const target = profile.value;
  if (!target || !isRelationalDriver(target.driver)) return;
  const key = contextKey(db, itemSchema); const query = schemaQuery(itemSchema);
  const [payload, catalog] = await Promise.all([
    api<Objects>(`/connections/${target.id}/databases/${encodeURIComponent(db)}/objects${query}`),
    api<Catalog>(`/connections/${target.id}/databases/${encodeURIComponent(db)}/catalog${query}`),
  ]);
  if (profile.value?.id !== target.id) return;
  objects.value = { ...objects.value, [key]: payload }; catalogs.value = { ...catalogs.value, [key]: catalog };
}
async function chooseDatabase(name: string) {
  database.value = name; const next = new Set(expanded.value); next.add(name); expanded.value = next;
  try {
    if (isSchemaDriver.value) { if (!schemas.value[name]) await loadSchemas(name); schema.value = defaultSchema(name); expandedSchemas.value = new Set([...expandedSchemas.value, schemaKey(name,schema.value)]); }
    else schema.value = "";
    if (!objects.value[contextKey(name,schema.value)]) await loadObjects(name,schema.value);
    const tab = active.value; if (tab?.type === "query" && (tab.database !== name || tab.schema !== schema.value)) await switchQueryContext(tab,name,schema.value);
  }
  catch (reason) { flashError(reason); }
}
async function toggleDatabase(name: string) {
  const next = new Set(expanded.value);
  if (next.has(name)) next.delete(name); else { next.add(name); if (isSchemaDriver.value) { if (!schemas.value[name]) await loadSchemas(name); } else if (!objects.value[name]) await loadObjects(name); }
  expanded.value = next;
}
async function chooseSchema(db: string, itemSchema: string) {
  database.value = db; schema.value = itemSchema; expanded.value = new Set([...expanded.value,db]); expandedSchemas.value = new Set([...expandedSchemas.value,schemaKey(db,itemSchema)]);
  try { if (!objects.value[contextKey(db,itemSchema)]) await loadObjects(db,itemSchema); const tab=active.value; if(tab?.type==="query"&&(tab.database!==db||tab.schema!==itemSchema)) await switchQueryContext(tab,db,itemSchema); } catch(reason){flashError(reason)}
}
async function toggleSchema(db: string, itemSchema: string) { const key=schemaKey(db,itemSchema); const next=new Set(expandedSchemas.value); if(next.has(key))next.delete(key);else{next.add(key);if(!objects.value[contextKey(db,itemSchema)])await loadObjects(db,itemSchema)}expandedSchemas.value=next; }
async function switchQueryContext(tab: QueryTab, db: string, itemSchema = "") {
  if (!profileForTab(tab) || (tab.database === db && tab.schema === itemSchema)) return;
  if (!tab.autocommit && !await confirmAction("切换数据库或 Schema 会回滚当前手动事务，确定继续吗？")) return;
  updateTab(tab.id, { loading: true, error: undefined });
  try {
    if (!catalogs.value[contextKey(db,itemSchema)]) await loadObjects(db,itemSchema);
    const session = await api<{ id: string }>("/sessions", { method: "POST", body: jsonBody({ profile_id: tab.profileId, database: db, schema:itemSchema, autocommit: tab.autocommit, workspace_id: workspaceId() }) });
    const old = tab.sessionId; updateTab(tab.id, { database: db, schema:itemSchema, sessionId: session.id, response: undefined, loading: false });
    void api(`/sessions/${old}`, { method: "DELETE" }).catch(() => undefined); database.value = db; schema.value=itemSchema; resultPane.value = "message"; flash(`查询标签已切换到 ${db}${itemSchema?`.`+itemSchema:""}`);
  } catch (reason) { updateTab(tab.id, { loading: false, error: errorText(reason) }); flashError(reason); }
}
async function switchQueryDatabase(tab: QueryTab, db: string) { if(isSchemaDriver.value&&!schemas.value[db])await loadSchemas(db); return switchQueryContext(tab,db,isSchemaDriver.value?defaultSchema(db):""); }
async function switchQuerySchema(tab: QueryTab, itemSchema: string) { return switchQueryContext(tab,tab.database,itemSchema); }
async function newQuery(db = database.value, sql = "SELECT 1 AS ready;", itemSchema = schema.value, draft?: { id?: string; title?: string; savedSql?: string }, profileId=profile.value?.id) {
  if (!profileId || !db) return;
  try { if (profileId===profile.value?.id&&!catalogs.value[contextKey(db,itemSchema)]) await loadObjects(db,itemSchema); const session = await api<{ id: string }>("/sessions", { method: "POST", body: jsonBody({ profile_id: profileId, database: db, schema:itemSchema, autocommit: true, workspace_id: workspaceId() }) }); const count = tabs.value.filter(tab => tab.type === "query"&&tab.profileId===profileId).length + 1; const tab: QueryTab = { id: draft?.id||uid(), profileId, type: "query", title: draft?.title || `查询 ${count}`, database: db, schema:itemSchema, sessionId: session.id, sql, savedSql: draft?.savedSql ?? sql, autocommit: true, loading: false, resultIndex: 0 }; tabs.value.push(tab); activateTab(tab); resultPane.value = "message"; }
  catch (reason) { flashError(reason); }
}
async function closeTab(tab: WorkTab, force = false) { const manual=tab.type==="query"&&!tab.autocommit;if(!force&&(queryDirty(tab)||manual)&&!await confirmAction(`查询“${tab.title}”${queryDirty(tab)?"有未保存修改":""}${queryDirty(tab)&&manual?"，且":""}${manual?"包含未提交事务":""}，确定关闭吗？`))return;if (tab.type === "query"&&tab.sessionId) void api(`/sessions/${tab.sessionId}`, { method: "DELETE" }).catch(() => undefined); const index = tabs.value.indexOf(tab);const wasActive=activeId.value===tab.id;tabs.value.splice(index, 1);if(lastActiveTabIds.get(tab.profileId)===tab.id)lastActiveTabIds.delete(tab.profileId);if(wasActive){const next=tabs.value[Math.max(0,index-1)];if(next)await selectTab(next);else activeId.value="";} }
async function closeTabs(targets: WorkTab[]) { const dirty=targets.filter(queryDirty).length;const manual=targets.filter(tab=>tab.type==="query"&&!tab.autocommit).length;if((dirty||manual)&&!await confirmAction(`${dirty} 个查询有未保存修改，${manual} 个查询包含未提交事务。确定关闭这些标签吗？`))return;for(const target of targets)await closeTab(target,true); }
function workspaceTabContext(event:MouseEvent){const header=(event.target as HTMLElement).closest('.arco-tabs-tab');if(!header)return;const index=Array.from((event.currentTarget as HTMLElement).querySelectorAll('.arco-tabs-tab')).indexOf(header);const tab=tabs.value[index];if(tab)tabMenu(event,tab);}
function selectTabKey(key:string|number){const tab=tabs.value.find(tab=>tab.id===key);if(tab)void selectTab(tab);}
function closeTabKey(key:string|number){const tab=tabs.value.find(tab=>tab.id===key);if(tab)void closeTab(tab);}
function tabMenu(event: MouseEvent, tab: WorkTab) { const index=tabs.value.indexOf(tab); contextMenu.value={x:event.clientX,y:event.clientY,title:`${tabTitle(tab)} · ${profileNameForTab(tab)}`,items:[{id:"close",label:"关闭",icon:"lucide:x",action:()=>closeTab(tab)},{id:"other",label:"关闭其他标签",icon:"lucide:copy-x",action:()=>closeTabs(tabs.value.filter(item=>item!==tab))},{id:"right",label:"关闭右侧标签",icon:"lucide:panel-right-close",disabled:index===tabs.value.length-1,action:()=>closeTabs([...tabs.value.slice(index+1)])},{id:"duplicate",label:"复制标签",icon:"lucide:copy",disabled:tab.type!=="query",action:async()=>{if(tab.type!=="query")return;await selectTab(tab);await newQuery(tab.database,tab.sql,tab.schema,undefined,tab.profileId)}},{id:"save",label:"保存查询…",icon:"lucide:save",disabled:tab.type!=="query",action:async()=>{if(tab.type!=="query")return;const name=await promptValue("查询名称",tab.title);if(!name)return;savedQueries.value=[{name,database:tab.database,schema:tab.schema,sql:tab.sql},...savedQueries.value.filter(item=>item.name!==name)];localStorage.setItem("deebee_saved_queries",JSON.stringify(savedQueries.value));tab.title=name;tab.savedSql=tab.sql;flash(`查询 ${name} 已保存`)}}]}; }
async function useSavedQuery(item:{name:string;database:string;schema?:string;sql:string}){const tab=active.value;if(tab?.type!=="query")return;if(item.schema!==undefined)await switchQueryContext(tab,item.database,item.schema);else await switchQueryDatabase(tab,item.database);tab.sql=item.sql;historyOpen.value=false}

async function sendQuery(tab: QueryTab, sql: string): Promise<QueryResponse> {
  const response = await api<QueryWireResponse>(`/sessions/${tab.sessionId}/query`, {
    method: "POST", body: jsonBody({ sql, limit: 10000 }), headers: { "X-Deebee-Row-Format": "array" },
  });
  return { ...response, results: response.results.map(result => {
    if (result.row_format !== "array") return result;
    const names = (result.columns || []).map(column => column.name);
    return { ...result, rows: result.rows.map(values => Object.fromEntries(names.map((name, index) => [name, values[index]]))) };
  }) };
}
function attachQueryResultSources(tab: QueryTab, sql: string, response: QueryResponse) {
  const statements = splitSqlStatements(sql);
  if (statements.length !== response.results.length) return;
  const catalog = catalogs.value[contextKey(tab.database,tab.schema)];
  if (!catalog) return;
  const postgres=profileForTab(tab)?.driver==="postgresql";
  response.results.forEach((result, index) => {
    if (result.kind !== "rows" || !result.columns?.length) return;
    const source = inferSingleSelectSource(statements[index]);
    const table = source && catalog.tables.find(item => item.name.toLowerCase() === source.table.toLowerCase());
    if (!source || !table) return;
    const actualColumns = new Set(table.columns.map(column => column.name.toLowerCase()));
    if (result.columns.some(column => !actualColumns.has(column.name.toLowerCase()))) return;
    const resultColumns = new Set(result.columns.map(column => column.name.toLowerCase()));
    const directSource = inferEditableSingleSelectSource(statements[index]);
    const primaryKey = directSource && !table.object_type.includes("VIEW") ? table.columns.filter(column => column.key === "PRI").map(column => column.name) : [];
    result.source = {
      database: postgres ? tab.database : source.qualifier || tab.database,
      schema: postgres ? source.qualifier || tab.schema : "",
      table: table.name,
      primaryKey: resultColumns.size === result.columns.length && primaryKey.length && primaryKey.every(column => resultColumns.has(column.toLowerCase())) ? primaryKey : undefined,
      statement: statements[index],
    };
  });
}
function acceptQueryResponse(tab: QueryTab, sql: string, response: QueryResponse) {
  attachQueryResultSources(tab, sql, response);
  updateTab(tab.id, { response, loading: false, autocommit: response.autocommit }); const firstRows = response.results.findIndex(result => result.kind === "rows"); resultPane.value = firstRows >= 0 ? `result-${firstRows}` : "message"; history.value = [sql, ...history.value.filter(item => item !== sql)].slice(0, 100); localStorage.setItem("deebee_history", JSON.stringify(history.value)); markBackendOnline();
}
async function runQuery(tab: QueryTab, sql = tab.sql) {
  if (!sql.trim()) return; updateTab(tab.id, { loading: true, error: undefined }); resultPane.value = "message";
  try { acceptQueryResponse(tab, sql, await sendQuery(tab, sql)); }
  catch (reason) {
    let failure = reason;
    if (shouldRecreateSession(reason)) {
      const transactionLost = !tab.autocommit;
      try {
        await recreateQuerySession(tab);
        if (transactionLost) {
          updateTab(tab.id, { loading: false, error: "查询会话已重建，但原手动事务已丢失；本条 SQL 未自动重放，请核对数据并重新开始事务。" });
          flash("连接已恢复，但原手动事务已丢失", true); return;
        }
        if (!safeToReplay(sql)) {
          updateTab(tab.id, { loading: false, error: "查询会话已重建。由于写入结果可能不确定，本条 SQL 未自动重放；请核对数据后再决定是否重试。" });
          flash("连接已恢复，写入 SQL 未自动重放", true); return;
        }
        acceptQueryResponse(tab, sql, await sendQuery(tab, sql)); flash("查询会话已自动重连，并安全重放只读查询"); return;
      }
      catch (retryReason) { failure = retryReason; }
    }
    if (isNetworkError(failure)) { connectionState.value = "offline"; updateTab(tab.id, { loading: false, error: "后台连接中断，正在自动重连。本条 SQL 未自动重放。" }); void checkConnection(false); }
    else updateTab(tab.id, { loading: false, error: errorText(failure) });
    resultPane.value = "message";
  }
}
async function cancelQuery(tab: QueryTab) { try { const result = await api<{ cancelled: boolean }>(`/sessions/${tab.sessionId}/cancel`, { method: "POST" }); flash(result.cancelled ? "查询已取消" : "当前没有运行中的查询"); } catch (reason) { flashError(reason); } }
async function saveQueryInline(tab: QueryTab, result: ResultSet, rowIndex: number, changes: Record<string, unknown>) {
  const source = result.source; const row = result.rows?.[rowIndex]; const primaryKey = source?.primaryKey || [];
  if (!tab.autocommit) return flash("手动事务模式下不能从结果网格独立提交修改", true);
  if (!profileForTab(tab) || !source || !row || !primaryKey.length) return flash("该查询结果不能安全地原地修改", true);
  const key = Object.fromEntries(primaryKey.map(field => [field, row[field]]));
  try {
    await api("/data/rows", { method: "PATCH", body: jsonBody({ profile_id: tab.profileId, database: source.database, schema: source.schema, table: source.table, key, changes }) });
    Object.assign(row, changes); flash("查询结果修改已保存");
  } catch (reason) {
    flashError(reason);
    if (source.statement) await runQuery(tab, source.statement);
  }
}
function saveActiveQueryInline(rowIndex: number, changes: Record<string, unknown>) { const tab=active.value;const result=activeResult.value;if(tab?.type==="query"&&result)void saveQueryInline(tab,result,rowIndex,changes); }
async function transaction(tab: QueryTab, action: "commit" | "rollback") { try { await api(`/sessions/${tab.sessionId}/${action}`, { method: "POST" }); flash(action === "commit" ? "事务已提交" : "事务已回滚"); } catch (reason) { flashError(reason); } }
async function toggleAutocommit(tab: QueryTab) { const enabling= !tab.autocommit;if(enabling&&!await confirmAction("开启自动提交会提交当前事务，确定继续吗？"))return;try { await api(`/sessions/${tab.sessionId}/autocommit`, { method: "PATCH", body: jsonBody({ enabled: enabling }) }); tab.autocommit = enabling; flash(tab.autocommit ? "当前事务已提交，并开启自动提交" : "已进入手动事务模式"); } catch (reason) { flashError(reason); } }

async function openData(table: string, db = database.value, itemSchema = schema.value) {
  inspector.value=undefined;
  if(!profile.value)return;const profileId=profile.value.id;const existing = tabs.value.find(tab => tab.type === "data" && tab.profileId===profileId&&tab.database === db && tab.schema === itemSchema && tab.table === table); if (existing) return selectTab(existing);
  const tab = reactive<DataTab>({ id: uid(), profileId, type: "data", title: table, database: db, schema:itemSchema, table, loading: true, page: 1, filterColumn: "", filterValue: "", selected: null }); tabs.value.push(tab); activateTab(tab); await loadData(tab);
}
async function loadData(tab: DataTab, overrides: Partial<DataTab> = {}) {
  if (!profileForTab(tab)) return; Object.assign(tab, overrides, { loading: true, error: undefined });
  try { const filters = tab.filterColumn && tab.filterValue ? [{ column: tab.filterColumn, operator: "contains", value: tab.filterValue }] : []; tab.data = await api<TableData>("/data/read", { method: "POST", body: jsonBody({ profile_id: tab.profileId, database: tab.database, schema:tab.schema, table: tab.table, page: tab.page, page_size: 100, limit: TABLE_DATA_LIMIT, filters, sort: tab.sort }) }); tab.loading = false; tab.selected = null; }
  catch (reason) { tab.loading = false; tab.error = errorText(reason); }
}
async function saveInline(tab: DataTab, rowIndex: number, changes: Record<string, unknown>) {
  if (!profileForTab(tab) || !tab.data) return; const row = tab.data.rows[rowIndex]; const key = Object.fromEntries(tab.data.primary_key.map(field => [field, row[field]]));
  if (!Object.keys(key).length) return flash("该表没有主键，不能安全地原地编辑", true);
  try { await api("/data/rows", { method: "PATCH", body: jsonBody({ profile_id: tab.profileId, database: tab.database, schema:tab.schema, table: tab.table, key, changes }) }); Object.assign(row, changes); flash("单元格修改已保存"); }
  catch (reason) { flashError(reason); await loadData(tab); }
}
async function persistGridEdits(profileId:string,databaseName:string,schemaName:string,table:string,primary:string[],rows:Record<string,unknown>[],edits:GridEdit[]) {
  if(!primary.length)throw new Error('数据没有主键，不能安全地编辑');
  const payload=edits.map(edit=>{const row=rows[edit.rowIndex];if(!row)throw new Error('记录已失效，请刷新后重试');return {key:Object.fromEntries(primary.map(name=>[name,row[name]])),changes:edit.changes};});
  const target={profile_id:profileId,database:databaseName,schema:schemaName,table};
  if(payload.length===1)await api('/data/rows',{method:'PATCH',body:jsonBody({...target,...payload[0]})});
  else await api('/data/rows/batch',{method:'PATCH',body:jsonBody({...target,edits:payload})});
  for(const edit of edits)Object.assign(rows[edit.rowIndex],edit.changes);
  flash(`已保存 ${edits.length} 条记录`);
}
async function saveDataEdits(tab:DataTab,edits:GridEdit[]){if(!tab.data)throw new Error('数据已失效');await persistGridEdits(tab.profileId,tab.database,tab.schema,tab.table,tab.data.primary_key,tab.data.rows,edits);}
async function saveQueryEdits(edits:GridEdit[]){const tab=active.value,result=activeResult.value,source=result?.source;if(tab?.type!=='query'||!result?.rows||!source||!tab.autocommit)throw new Error('当前结果不能独立提交修改');await persistGridEdits(tab.profileId,source.database,source.schema,source.table,source.primaryKey||[],result.rows,edits);}
async function exportGrid(format:string,rows:Record<string,unknown>[],fields:string[]){
  const tab=active.value;if(!tab||!isDatabaseTab(tab))return;
  const result=tab.type==='query'?activeResult.value:undefined;const source=tab.type==='data'?{table:tab.table,schema:tab.schema}:result?.source;
  if(format==='sql'&&!source){flash('SQL 导出需要可识别的单表结果；此结果可导出 CSV、Excel 或 JSON',true);return;}
  const definitions=tab.type==='data'?tab.data?.columns:result?.columns;
  try{await download(`${API_BASE}/data/export-result`,`${source?.table||'query_result'}.${format}`,{method:'POST',body:jsonBody({profile_id:tab.profileId,table:source?.table||'query_result',schema:source?.schema||'',format,columns:fields.map(name=>definitions!.find(column=>column.name===name)),rows})});flash(`已导出 ${rows.length} 行`);}catch(reason){flashError(reason);}
}
async function insertRow(tab: DataTab) {
  if (!profileForTab(tab) || !rowDialog.value) return;
  try {
    const values = Object.fromEntries(Object.entries(rowDialog.value.valueModes).flatMap(([field, mode]) => mode === "default" ? [] : [[field, mode === "null" ? null : parseCellValue(rowDialog.value!.values[field] ?? "", tab.data!.columns.find(column=>column.name===field)!)]]));
    await api("/data/rows", { method: "POST", body: jsonBody({ profile_id: tab.profileId, database: tab.database, schema:tab.schema, table: tab.table, values }) }); rowDialog.value = undefined; await loadData(tab); flash("记录已添加");
  } catch (reason) { flashError(reason); }
}
async function deleteRow(tab: DataTab) { if (!profileForTab(tab) || tab.selected == null || !tab.data) return flash("请先选择一行"); const row = tab.data.rows[tab.selected]; const key = Object.fromEntries(tab.data.primary_key.map(field => [field, row[field]])); if (!await confirmAction("确定删除选中记录吗？")) return; try { await api("/data/rows", { method: "DELETE", body: jsonBody({ profile_id: tab.profileId, database: tab.database, schema:tab.schema, table: tab.table, key }) }); await loadData(tab); flash("记录已删除"); } catch (reason) { flashError(reason); } }
function sortData(tab: DataTab, column: string) { const direction = tab.sort?.column === column && tab.sort.direction === "asc" ? "desc" : "asc"; void loadData(tab, { sort: { column, direction }, page: 1 }); }
function reloadActiveData(overrides: Partial<DataTab> = {}) { if (activeData.value) void loadData(activeData.value, overrides); }
function deleteActiveRow() { if (activeData.value) void deleteRow(activeData.value); }
function saveActiveInline(row: number, changes: Record<string, unknown>) { if (activeData.value) void saveInline(activeData.value, row, changes); }
function sortActiveData(column: string) { if (activeData.value) sortData(activeData.value, column); }
function insertActiveRow() { if (activeData.value) void insertRow(activeData.value); }

async function openDesigner(table?: string, db = database.value, itemSchema = schema.value) {
  inspector.value=undefined;
  if (!profile.value || !db) return; const profileId=profile.value.id;const existing = tabs.value.find(tab => tab.type === "designer" && tab.profileId===profileId&&tab.database === db && tab.schema===itemSchema && tab.currentTable === (table || null)); if (existing) return selectTab(existing);
  const tab = reactive<DesignerTab>({ id: uid(), profileId, type: "designer", title: table ? `${table} — 设计` : "新建表", database: db, schema:itemSchema, table: table || "new_table", currentTable: table || null, loading: true, pane: "columns", statements: [], dangerous: [] }); tabs.value.push(tab); activateTab(tab);
  await loadDesignerTab(tab);
}
async function loadDesignerTab(tab: DesignerTab) {
  const targetProfile=profileForTab(tab);if(!targetProfile)return;
  tab.loading=true;tab.error=undefined;
  try { let source: TableSchema | undefined; if (tab.currentTable) source = await api<TableSchema>(`/schema/${tab.profileId}/${encodeURIComponent(tab.database)}/${encodeURIComponent(tab.currentTable)}${schemaQuery(tab.schema)}`); const pg=targetProfile.driver==="postgresql"; tab.source = source; tab.spec = source ? { database: tab.database, schema:tab.schema, table: source.table, columns: source.columns.map(column=>({...column,original_name:column.name})), primary_key: source.primary_key, indexes: source.indexes, foreign_keys: source.foreign_keys, checks: source.checks, engine: source.engine, charset: source.charset, collation: source.collation, comment: source.comment } : { database: tab.database, schema:tab.schema, table: "new_table", columns: [{ name: "id", data_type: pg?"BIGINT":"BIGINT UNSIGNED", nullable: false, default: null, extra: pg?"IDENTITY":"AUTO_INCREMENT", comment: "", generation: "" }], primary_key: ["id"], indexes: [], foreign_keys: [], checks: [], engine: pg?"PostgreSQL":"InnoDB", charset: pg?"UTF8":"utf8mb4", collation: pg?"":"utf8mb4_unicode_ci", comment: "" }; tab.loading = false; }
  catch (reason) { tab.loading = false; tab.error = errorText(reason); }
}
function updateSpec(tab: DesignerTab, patch: Partial<TableSpec>) { if (tab.spec) tab.spec = { ...tab.spec, ...patch }; tab.statements = []; tab.dangerous = []; }
async function previewDdl(tab: DesignerTab) { if (!profileForTab(tab) || !tab.spec) return; tab.loading = true; try { const result = await api<{ statements: string[]; dangerous: string[] }>("/ddl/preview", { method: "POST", body: jsonBody({ profile_id: tab.profileId, spec: tab.spec, current_table: tab.currentTable }) }); tab.statements = result.statements; tab.dangerous = result.dangerous; tab.pane = "ddl"; } catch (reason) { tab.error = errorText(reason); } finally { tab.loading = false; } }
async function applyDdl(tab: DesignerTab) { if (!profileForTab(tab) || !tab.spec || !tab.statements.length) return; if (tab.dangerous.length && !await confirmAction("检测到危险结构变更，确定执行吗？")) return; try { await api("/ddl/apply", { method: "POST", body: jsonBody({ profile_id: tab.profileId, spec: tab.spec, current_table: tab.currentTable, expected_statements: tab.statements }) }); tab.currentTable = tab.spec.table; tab.title = `${tab.spec.table} — 设计`; tab.spec.columns=tab.spec.columns.map(column=>({...column,original_name:column.name}));tab.statements = []; tab.dangerous = []; await loadObjects(tab.database,tab.schema); flash("表结构已保存"); } catch (reason) { flashError(reason); } }

function openList(kind: keyof Objects, db: string, itemSchema="") { if(!profile.value)return;const profileId=profile.value.id;const id = `objects:${profileId}:${contextKey(db,itemSchema)}:${kind}`; const existing = tabs.value.find(tab => tab.id === id); if (existing) return selectTab(existing); const labels = { tables: "表", views: "视图", routines: "函数与过程", triggers: "触发器", events: "事件" }; const tab: ObjectsTab = { id, profileId, type: "objects", title: labels[kind], database: db, schema:itemSchema, kind }; tabs.value.push(tab); activateTab(tab); }
function objectQuery(itemSchema:string,objectId?:number){const params=new URLSearchParams();if(itemSchema)params.set("schema",itemSchema);if(objectId)params.set("object_id",String(objectId));const value=params.toString();return value?`?${value}`:""}
type ObjectDefinition = {sql:string;metadata?:Record<string,unknown>};
function definitionSql(result:ObjectDefinition){
  // Older MySQL responses return trigger DDL under this SHOW CREATE column.
  const sql=result.sql||(typeof result.metadata?.["SQL Original Statement"]==="string"?result.metadata["SQL Original Statement"]:"");
  if(!sql)throw new Error("服务器未返回对象定义");
  return sql;
}
async function openObject(kind: string, item: DbObject, db: string, itemSchema="") { if (!profile.value) return; const normalized = item.object_type === "MATERIALIZED VIEW" ? "materialized view" : kind === "routines" ? item.object_type.toLowerCase() : kind.replace(/s$/, ""); try { const result = await api<ObjectDefinition>(`/objects/${profile.value.id}/${encodeURIComponent(db)}/${normalized}/${encodeURIComponent(item.name)}/ddl${objectQuery(itemSchema,item.object_id)}`); await newQuery(db, definitionSql(result),itemSchema); } catch (reason) { flashError(reason); } }
function inspect(item: DbObject, db: string, itemSchema="") { inspector.value = { title: item.name, subtitle: `${db}${itemSchema?`.`+itemSchema:""} · ${item.object_type}`, lines: [`预计行数：${item.estimated_rows ?? "—"}`, `引擎：${item.engine || "—"}`, `数据大小：${item.data_length ?? "—"}`, `索引大小：${item.index_length ?? "—"}`, `排序规则：${item.collation || "—"}`, `创建时间：${item.created_at || "—"}`, `更新时间：${item.updated_at || "—"}`, `注释：${item.comment || "—"}`] }; }

async function refreshDatabase(db: string, itemSchema = schema.value) { try { if(isSchemaDriver.value)await loadSchemas(db); await loadObjects(db,itemSchema); flash(`${db}${itemSchema?`.`+itemSchema:""} 已刷新`); } catch (reason) { flashError(reason); } }
function openCreateDatabase() { createDatabaseDialog.value = { name: "", charset: isPostgres.value?"UTF8":isMssql.value?"Unicode":"utf8mb4", collation: isMssql.value?"":isPostgres.value?"":"utf8mb4_unicode_ci", recovery_model:"SIMPLE", data_size_mb:32, log_size_mb:16, filegrowth_mb:64, saving: false }; }
async function refreshServer() {
  if (!profile.value) return;
  if(!isRelationalDriver(profile.value.driver)){await testCurrentConnection();return;}
  try {
    databases.value = await api<Database[]>(`/connections/${profile.value.id}/databases`);
    await Promise.all(databases.value.filter(db=>expanded.value.has(db.name)).map(async db=>{
      if(isSchemaDriver.value){
        await loadSchemas(db.name);
        await Promise.all((schemas.value[db.name]||[]).filter(item=>expandedSchemas.value.has(schemaKey(db.name,item.name))).map(item=>loadObjects(db.name,item.name)));
      }else await loadObjects(db.name,"");
    }));
    flash(`${profile.value.name} 已刷新`);
  } catch (reason) { flashError(reason); }
}
async function createDatabase() {
  const dialog = createDatabaseDialog.value;
  if (!profile.value || !dialog || !dialog.name.trim()) return;
  dialog.saving = true;
  try {
    const name = dialog.name.trim();
    const options=profile.value.driver==="mssql"?{recovery_model:dialog.recovery_model,data_size_mb:dialog.data_size_mb,log_size_mb:dialog.log_size_mb,filegrowth_mb:dialog.filegrowth_mb}:{};
    await api("/databases", { method: "POST", body: jsonBody({ profile_id: profile.value.id, name, charset: dialog.charset, collation: dialog.collation, options }) });
    databases.value = await api<Database[]>(`/connections/${profile.value.id}/databases`);
    serverExpanded.value = true; database.value = name; expanded.value = new Set([...expanded.value, name]);
    if(isSchemaDriver.value){await loadSchemas(name);schema.value=defaultSchema(name);expandedSchemas.value=new Set([...expandedSchemas.value,schemaKey(name,schema.value)])}else schema.value="";
    await loadObjects(name,schema.value); createDatabaseDialog.value = undefined; flash(`数据库 ${name} 已创建`);
  } catch (reason) { flashError(reason); } finally { if (createDatabaseDialog.value) createDatabaseDialog.value.saving = false; }
}
function serverMenu(event: MouseEvent) {
  if (!profile.value) return;
  contextMenu.value = { x: event.clientX, y: event.clientY, title: `${profile.value.name} 服务器操作`, items: [
    { id:"toggle", label:serverExpanded.value?"收起服务器":"展开服务器", icon:"lucide:server", action:()=>serverExpanded.value=!serverExpanded.value },
    { id:"properties", label:"连接属性…", icon:"lucide:settings-2", action:()=>inspector.value={title:profile.value!.name,subtitle:`${driverLabel(profile.value!.driver)} 数据库服务器`,lines:[`主机：${profile.value!.host}`,`端口：${profile.value!.port}`,`用户：${profile.value!.user}`,`默认数据库：${profile.value!.default_database || "—"}`,`默认 Schema：${profile.value!.default_schema || "—"}`]} },
    { id:"edit-connection", label:"编辑连接…", icon:"lucide:server-cog", action:openEditConnection },
    { id:"duplicate-connection", label:"复制连接…", icon:"lucide:copy-plus", action:()=>openCreateConnection(profile.value) },
    { id:"test", label:"测试连接", icon:"lucide:plug-zap", action:()=>api(`/connections/${profile.value!.id}/test`,{method:"POST"}).then(()=>flash("连接成功")).catch(flashError) },
    { id:"s1", separator:true },
    { id:"new-connection", label:"新建连接…", icon:"lucide:server-crash", action:()=>openCreateConnection() },
    { id:"new-db", label:"新建数据库…", icon:"lucide:database-backup", action:openCreateDatabase },
    { id:"query", label:"新建查询", icon:"lucide:square-terminal", action:()=>newQuery() },
    { id:"refresh", label:"刷新", icon:"lucide:refresh-cw", action:refreshServer },
    { id:"s2", separator:true },
    { id:"delete-connection", label:"删除连接", icon:"lucide:trash-2", danger:true, action:()=>deleteConnection() },
  ] };
}
function showConnectionProperties(item: Profile) {
  inspector.value = { title:item.name, subtitle:`${driverLabel(item.driver)} 连接`, lines:[`主机：${item.host}`,`端口：${item.port}`,`用户：${item.user || "—"}`,...(!isRemoteDriver(item.driver)?[`默认数据库：${item.default_database || "—"}`]:[])] };
}
async function testProfileConnection(item: Profile) {
  try { const result=await api<{latency_ms:number}>(`/connections/${item.id}/test`,{method:"POST"});flash(`${item.name} 连接正常 · ${result.latency_ms} ms`); }
  catch(reason){ flashError(reason); }
}
function connectionMenu(event: MouseEvent, item: Profile) {
  if (item.id === profile.value?.id && isRelationalDriver(item.driver)) return serverMenu(event);
  const current = item.id === profile.value?.id;
  contextMenu.value = { x:event.clientX, y:event.clientY, title:`${item.name} 连接操作`, items:[
    { id:"open", label:current?"当前连接":"打开连接", icon:"lucide:log-in", disabled:current, action:()=>switchProfile(item.id) },
    ...(current&&isRemoteDriver(item.driver)?[{ id:"session", label:item.driver==="ssh"?"新建终端":"新建桌面", icon:item.driver==="ssh"?"lucide:square-terminal":"lucide:monitor-up", action:()=>openRemoteSession(item.driver) }]:[]),
    ...(item.driver==="ssh"?[{ id:"files", label:"文件管理", icon:"lucide:folder-open", action:()=>{fileManagerProfile.value=item;} }]:[]),
    { id:"properties", label:"连接属性…", icon:"lucide:info", action:()=>showConnectionProperties(item) },
    { id:"edit-connection", label:"编辑连接…", icon:"lucide:server-cog", action:()=>openEditConnection(item) },
    { id:"duplicate-connection", label:"复制连接…", icon:"lucide:copy-plus", action:()=>openCreateConnection(item) },
    { id:"test", label:"测试连接", icon:"lucide:plug-zap", action:()=>testProfileConnection(item) },
    { id:"s1", separator:true },
    { id:"new-connection", label:"新建连接…", icon:"lucide:server-crash", action:()=>openCreateConnection() },
    { id:"delete-connection", label:"删除连接", icon:"lucide:trash-2", danger:true, action:()=>deleteConnection(item) },
  ] };
}
function synchronizeTableTabs(profileId:string,db:string,itemSchema:string,table:string,action:string,target:string){const affected=tabs.value.filter((tab):tab is DataTab|DesignerTab=>{if(tab.type!=="data"&&tab.type!=="designer")return false;return tab.profileId===profileId&&tab.database===db&&tab.schema===itemSchema&&(tab.type==="data"?tab.table:tab.currentTable)===table});if(action==="drop"){for(const tab of affected)void closeTab(tab);return}if(action!=="rename"||!target)return;for(const tab of affected){if(tab.type==="data"){tab.table=target;tab.title=target}else{tab.currentTable=target;tab.table=target;if(tab.spec)tab.spec={...tab.spec,table:target};tab.title=`${target} — 设计`}}}
async function tableAction(db: string, itemSchema:string, table: string, action: string, target = "", withData = false) { if (!profile.value) return; const profileId=profile.value.id;try { const result = await api<{ results?: Record<string,unknown>[] }>("/objects/table/action", { method: "POST", body: jsonBody({ profile_id: profileId, database: db, schema:itemSchema, table, action, target, with_data: withData }) }); if (["drop","rename","duplicate"].includes(action)) await loadObjects(db,itemSchema); synchronizeTableTabs(profileId,db,itemSchema,table,action,target); const tab = tabs.value.find(item => item.type === "data" && item.profileId===profileId&&item.database === db && item.schema===itemSchema && item.table === table); if (tab?.type === "data" && ["empty","truncate"].includes(action)) await loadData(tab); if (result.results?.length) inspector.value = { title: `${table} · 维护`, subtitle: action.toUpperCase(), lines: result.results.map(row => Object.values(row).join(" · ")) }; flash(`${table}：操作已完成`); } catch (reason) { flashError(reason); } }
function databaseMenu(event: MouseEvent, db: Database) { database.value = db.name; contextMenu.value = { x: event.clientX, y: event.clientY, title: `${db.name} 数据库操作`, items: [
  { id:"toggle",label:expanded.value.has(db.name)?"关闭数据库":"打开数据库",icon:"lucide:database",action:()=>toggleDatabase(db.name) }, { id:"properties",label:"编辑数据库…",icon:"lucide:info",action:()=>inspector.value={title:db.name,subtitle:"数据库属性",lines:[`字符集：${db.charset}`,`排序规则：${db.collation}`]} }, { id:"new-db",label:"新建数据库…",icon:"lucide:database-backup",action:openCreateDatabase }, { id:"drop-db",label:"删除数据库",icon:"lucide:trash-2",danger:true,action:async()=>{if(!profile.value||await promptValue(`输入 ${db.name} 确认删除`)!==db.name)return;await api(`/databases/${profile.value.id}/${encodeURIComponent(db.name)}`,{method:"DELETE"});databases.value=databases.value.filter(item=>item.name!==db.name);flash(`${db.name} 已删除`)} }, {id:"s1",separator:true},
  {id:"query",label:"新建查询",icon:"lucide:square-terminal",shortcut:"⌘Y",action:()=>newQuery(db.name,"",isSchemaDriver.value?defaultSchema(db.name):"")},{id:"console",label:"控制台",icon:"lucide:terminal",action:()=>newQuery(db.name,isPostgres.value?"SELECT * FROM pg_stat_activity;\n":isMssql.value?"SELECT * FROM sys.dm_exec_sessions;\n":"SHOW STATUS;\n",isSchemaDriver.value?defaultSchema(db.name):"")},{id:"sql",label:"执行 SQL 文件…",icon:"lucide:file-input",action:()=>openWizard({kind:"sql",database:db.name,schema:isSchemaDriver.value?defaultSchema(db.name):""})},{id:"dump",label:"转储 SQL 文件…",icon:"lucide:download",action:()=>openWizard({kind:"dump",database:db.name,schema:isSchemaDriver.value?defaultSchema(db.name):""})},{id:"print",label:"打印数据库…",icon:"lucide:printer",action:printWorkspace},{id:"search",label:"在数据库中查找…",icon:"lucide:search",action:()=>findDatabase(db.name,isSchemaDriver.value?defaultSchema(db.name):"")},{id:"refresh",label:"刷新",icon:"lucide:refresh-cw",shortcut:"⌘R",action:()=>refreshDatabase(db.name,isSchemaDriver.value?defaultSchema(db.name):"")}] };
}
function tableMenu(event: MouseEvent, table: DbObject, db: string, itemSchema="") { const confirmTableAction=(label:string,action:string)=>async()=>{if(await confirmAction(`确定${label}表 ${table.name} 吗？`))void tableAction(db,itemSchema,table.name,action)}; contextMenu.value={x:event.clientX,y:event.clientY,title:`${table.name} 表操作`,items:[
  {id:"open",label:"打开表",icon:"lucide:table-2",action:()=>openData(table.name,db,itemSchema)},{id:"design",label:"设计表",icon:"lucide:panel-top-open",disabled:isMssql.value,action:()=>openDesigner(table.name,db,itemSchema)},{id:"new",label:"新建表",icon:"lucide:plus",disabled:isMssql.value,action:()=>openDesigner(undefined,db,itemSchema)},{id:"drop",label:"删除表",icon:"lucide:trash-2",danger:true,action:confirmTableAction("删除","drop")},{id:"empty",label:"清空表",icon:"lucide:eraser",danger:true,action:confirmTableAction("清空","empty")},{id:"truncate",label:"Truncate 表",icon:"lucide:scissors",danger:true,action:confirmTableAction("截断","truncate")},
  {id:"duplicate",label:"复制表",icon:"lucide:copy",disabled:isMssql.value,children:[{id:"structure",label:"仅结构…",icon:"lucide:copy",action:async()=>{const target=await promptValue("新表名称",`${table.name}_copy`);if(target)await tableAction(db,itemSchema,table.name,"duplicate",target,false)}},{id:"data",label:"结构和数据…",icon:"lucide:copy-plus",action:async()=>{const target=await promptValue("新表名称",`${table.name}_copy`);if(target)await tableAction(db,itemSchema,table.name,"duplicate",target,true)}}]},
  {id:"priv",label:"查看权限…",icon:"lucide:user-round-cog",action:()=>showPrivileges(db,table.name,itemSchema)},{id:"s1",separator:true},{id:"import",label:"导入向导…",icon:"lucide:file-input",action:()=>openWizard({kind:"import",database:db,schema:itemSchema,table:table.name})},{id:"export",label:"导出向导…",icon:"lucide:file-output",action:()=>openWizard({kind:"export",database:db,schema:itemSchema,table:table.name})},{id:"generate",label:"数据生成…",icon:"lucide:sparkles",disabled:isMssql.value,action:()=>openWizard({kind:"generate",database:db,schema:itemSchema,table:table.name})},{id:"dump",label:"转储 SQL 文件…",icon:"lucide:download",action:()=>openWizard({kind:"dump",database:db,schema:itemSchema,table:table.name})},{id:"maintain",label:"维护",icon:"lucide:wrench",children:(isPostgres.value||isMssql.value?["analyze"]:["check","analyze","optimize","repair"]).map(action=>({id:action,label:isMssql.value?"UPDATE STATISTICS":`${action.toUpperCase()} TABLE`,icon:"lucide:wrench",action:()=>tableAction(db,itemSchema,table.name,action)}))},{id:"s2",separator:true},{id:"copy-name",label:"复制名称",icon:"lucide:copy",action:()=>navigator.clipboard.writeText(sqlTargetName({database:db,schema:itemSchema,table:table.name}))},{id:"copy-ddl",label:"复制 DDL",icon:"lucide:clipboard-copy",action:()=>copyDdl(db,"table",table.name,itemSchema)},{id:"rename",label:"重命名…",icon:"lucide:pencil",action:async()=>{const target=await promptValue("新表名称",table.name);if(target&&target!==table.name)await tableAction(db,itemSchema,table.name,"rename",target)}},{id:"refresh",label:"刷新",icon:"lucide:refresh-cw",action:()=>refreshDatabase(db,itemSchema)}]}; }
function objectMenu(event: MouseEvent, kind: string, item: DbObject, db: string, itemSchema="") { const normalized=item.object_type==="MATERIALIZED VIEW"?"materialized view":kind==="routines"?item.object_type.toLowerCase():kind.replace(/s$/,""); const canToggle=normalized==="event"||(normalized==="trigger"&&isPostgres.value);const eventItems=canToggle?[{id:"enable",label:"启用",icon:"lucide:circle-play",disabled:item.status==="ENABLED",action:()=>objectStateAction(db,itemSchema,normalized,item,"enable")},{id:"disable",label:"禁用",icon:"lucide:circle-pause",disabled:item.status==="DISABLED",action:()=>objectStateAction(db,itemSchema,normalized,item,"disable")}]:[]; contextMenu.value={x:event.clientX,y:event.clientY,title:`${item.name} 对象操作`,items:[{id:"open",label:"打开定义",icon:"lucide:file-code",action:()=>openObject(kind,item,db,itemSchema)},{id:"edit",label:"编辑定义 SQL",icon:"lucide:file-pen-line",action:()=>openObject(kind,item,db,itemSchema)},...eventItems,{id:"copy",label:"复制名称",icon:"lucide:copy",action:()=>navigator.clipboard.writeText(item.name)},{id:"ddl",label:"复制 DDL",icon:"lucide:clipboard-copy",action:()=>copyDdl(db,normalized,item,itemSchema)},{id:"drop",label:"删除对象",icon:"lucide:trash-2",danger:true,action:async()=>{if(!profile.value||!await confirmAction(`确定删除 ${item.name}${item.identity_arguments!==undefined?`(${item.identity_arguments})`:""} 吗？`))return;await api("/objects/action",{method:"POST",body:jsonBody({profile_id:profile.value.id,database:db,schema:itemSchema,kind:normalized,name:item.name,object_id:item.object_id,action:"drop"})});await loadObjects(db,itemSchema);flash(`${item.name} 已删除`)}},{id:"refresh",label:"刷新",icon:"lucide:refresh-cw",action:()=>refreshDatabase(db,itemSchema)}]}; }
async function objectStateAction(db:string,itemSchema:string,kind:string,item:DbObject,action:"enable"|"disable"){if(!profile.value)return;try{await api("/objects/action",{method:"POST",body:jsonBody({profile_id:profile.value.id,database:db,schema:itemSchema,kind,name:item.name,action})});await loadObjects(db,itemSchema);flash(`${item.name} 已${action==="enable"?"启用":"禁用"}`)}catch(reason){flashError(reason)}}
async function copyDdl(db:string,kind:string,item:DbObject|string,itemSchema=""){if(!profile.value)return;const object:DbObject=typeof item==="string"?{name:item,object_type:kind}:item;try{const result=await api<ObjectDefinition>(`/objects/${profile.value.id}/${encodeURIComponent(db)}/${kind}/${encodeURIComponent(object.name)}/ddl${objectQuery(itemSchema,object.object_id)}`);await navigator.clipboard.writeText(definitionSql(result));flash("DDL 已复制")}catch(reason){flashError(reason)}}
async function showPrivileges(db:string,table:string,itemSchema=""){if(!profile.value)return;try{const payload=await api<Record<string,unknown>[]|{account?:string;grants?:unknown[]}>(`/privileges/${profile.value.id}/${encodeURIComponent(db)}?table=${encodeURIComponent(table)}&schema=${encodeURIComponent(itemSchema)}`);const lines=Array.isArray(payload)?payload.map(row=>Object.values(row).join(" · ")):[...(payload.account?[`账户：${payload.account}`]:[]),...(payload.grants||[]).map(grant=>typeof grant==="string"?grant:JSON.stringify(grant))];inspector.value={title:`${table||itemSchema||db} · 权限`,subtitle:`${db}${itemSchema?`.`+itemSchema:""}`,lines:lines.length?lines:["未返回权限记录"]}}catch(reason){flashError(reason)}}
async function findDatabase(db:string,itemSchema=""){if(!profile.value)return;const term=await promptValue("查找表、字段、视图、函数或触发器");if(!term)return;try{const rows=await api<{kind:string;name:string;detail:string}[]>(`/search/${profile.value.id}/${encodeURIComponent(db)}?q=${encodeURIComponent(term)}&schema=${encodeURIComponent(itemSchema)}`);database.value=db;schema.value=itemSchema;searchDialog.value={term,rows}}catch(reason){flashError(reason)}}
function openSearchResult(row:{kind:string;name:string}){if(row.kind==="function"||row.kind==="procedure"){void openObject("routines",{name:row.name,object_type:row.kind.toUpperCase()},database.value,schema.value);searchDialog.value=undefined;return}const table=row.kind==="column"?row.name.split(".")[0]:row.name;const target=sqlTargetName({database:database.value,schema:schema.value,table});void newQuery(database.value,isMssql.value?`SELECT TOP (100) * FROM ${target};`:`SELECT * FROM ${target} LIMIT 100;`,schema.value);searchDialog.value=undefined}
function categoryMenu(event: MouseEvent, kind: keyof Objects, db: string, itemSchema="") {
  const names: Record<keyof Objects,string>={tables:"表",views:"视图",routines:"函数与过程",triggers:"触发器",events:"事件"};
  const create: MenuItem = kind==="tables"
    ? {id:"create",label:"新建表",icon:"lucide:table-2",action:()=>openDesigner(undefined,db,itemSchema)}
    : {id:"create",label:"新建定义 SQL",icon:"lucide:file-plus",action:()=>newQuery(db,kind==="views"?"CREATE VIEW view_name AS\nSELECT 1 AS value;":kind==="routines"?"-- 在当前上下文编写 CREATE FUNCTION / CREATE PROCEDURE\n":kind==="triggers"?"-- 在当前上下文编写 CREATE TRIGGER\n":"-- 在当前上下文编写 CREATE EVENT\n",itemSchema)};
  contextMenu.value={x:event.clientX,y:event.clientY,title:`${db}${itemSchema?'.'+itemSchema:''} · ${names[kind]}`,items:[
    {id:"open",label:"打开对象列表",icon:"lucide:folder-open",action:()=>openList(kind,db,itemSchema)},create,
    {id:"query",label:"新建查询",icon:"lucide:square-terminal",action:()=>newQuery(db,undefined,itemSchema)},
    {id:"refresh",label:"刷新",icon:"lucide:refresh-cw",action:()=>refreshDatabase(db,itemSchema)},
  ]};
}
function objectListMenu(event: MouseEvent, item: DbObject) { const tab = activeObjectsTab.value; if (!tab) return; if (tab.kind === "tables") tableMenu(event, item, tab.database,tab.schema); else objectMenu(event, tab.kind, item, tab.database,tab.schema); }

type SqlTableTarget = { database: string; schema: string; table: string; primaryKey?: string[] };
function sqlIdentifier(name: string) { if(isMssql.value)return `[${name.replaceAll("]", "]]" )}]`;const mark = isPostgres.value ? '"' : "`"; return `${mark}${name.replaceAll(mark, mark + mark)}${mark}`; }
function sqlTargetName(target?: SqlTableTarget) { if (!target) return "table_name"; const namespace = isSchemaDriver.value ? target.schema : target.database; return [namespace, target.table].filter(Boolean).map(sqlIdentifier).join("."); }
function sqlLiteral(value: unknown, dataType = ""): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "boolean") return isMssql.value?(value?"1":"0"):(value ? "TRUE" : "FALSE");
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  if (Array.isArray(value) && isPostgres.value && dataType.trim().endsWith("[]")) return `ARRAY[${value.map(item => sqlLiteral(item, dataType.replace(/\[\]$/, ""))).join(", ")}]`;
  if (typeof value === "object" && "$binary" in value && typeof (value as { $binary?: unknown }).$binary === "string") {
    const hex = (value as { $binary: string }).$binary;
    return isPostgres.value ? `decode('${hex}','hex')` : isMssql.value?`0x${hex}`:`X'${hex}'`;
  }
  const raw = typeof value === "object" ? JSON.stringify(value) : String(value);
  const escaped = raw.replaceAll("'", "''").replaceAll("\\", isPostgres.value ? "\\" : "\\\\");
  return `${isMssql.value?"N":""}'${escaped}'`;
}
async function printWorkspace() {
  const grid = resultGridRef.value;
  try { await grid?.preparePrint(); window.print(); }
  finally { grid?.finishPrint(); }
}
function resultMenu(event: MouseEvent, rowIndex: number, column: string, result: ResultSet, target?: SqlTableTarget) {
  const row=result.rows?.[rowIndex]||{}; const columnItems=result.columns||[]; const cols=columnItems.map(item=>item.name); const tableName=sqlTargetName(target); const fields=cols.map(sqlIdentifier); const literal=(name:string)=>sqlLiteral(row[name],columnItems.find(item=>item.name===name)?.type); const copy=(value:string)=>navigator.clipboard.writeText(value).then(()=>flash("已复制到剪贴板")); const keys=target?.primaryKey?.filter(key=>cols.includes(key))||[];
  const copyAs: MenuItem[] = target ? [{id:"insert",label:"INSERT Statement",icon:"lucide:file-plus",action:()=>copy(`INSERT INTO ${tableName} (${fields.join(", ")}) VALUES (${cols.map(literal).join(", ")});`)}] : [{id:"insert-unavailable",label:"INSERT Statement（仅支持可识别的单表结果）",icon:"lucide:file-question",disabled:true}];
  if (keys.length && cols.some(name => !keys.includes(name))) copyAs.push({id:"update",label:"UPDATE Statement",icon:"lucide:file-pen",action:()=>copy(`UPDATE ${tableName} SET ${cols.filter(name=>!keys.includes(name)).map(name=>`${sqlIdentifier(name)} = ${literal(name)}`).join(", ")} WHERE ${keys.map(name=>`${sqlIdentifier(name)} = ${literal(name)}`).join(" AND ")};`)});
  copyAs.push({id:"tsv-data",label:"Tab Separated Values (Data only)",icon:"lucide:rows-3",action:()=>copy(cols.map(c=>String(row[c]??"")).join("\t"))},{id:"tsv-fields",label:"Tab Separated Values (Field Name only)",icon:"lucide:columns-3",action:()=>copy(cols.join("\t"))},{id:"tsv-all",label:"Tab Separated Values (Field Name and Data)",icon:"lucide:table-2",action:()=>copy(`${cols.join("\t")}\n${cols.map(c=>String(row[c]??"")).join("\t")}`)},{id:"json",label:"JSON",icon:"lucide:braces",action:()=>copy(JSON.stringify(row,null,2))});
  contextMenu.value={x:event.clientX,y:event.clientY,title:"结果表格操作",items:[{id:"edit-cell",label:"弹窗编辑单元格…",icon:"lucide:square-pen",shortcut:"Shift+Enter",action:()=>resultGridRef.value?.editCurrentCell()},{id:"copy-selection",label:"复制所选区域",icon:"lucide:copy",shortcut:"Ctrl/⌘+C",action:()=>resultGridRef.value?.copySelection()},{id:"paste-selection",label:"粘贴数据…",icon:"lucide:clipboard-paste",shortcut:"Ctrl/⌘+V",disabled:!keys.length,action:()=>resultGridRef.value?.pasteFromClipboard()},{id:"set-null",label:"设为 NULL",icon:"lucide:circle-slash",disabled:!keys.length,action:()=>resultGridRef.value?.setNull()},{id:"copy-cell",label:"复制单元格",icon:"lucide:copy",action:()=>copy(typeof row[column]==="object"?JSON.stringify(row[column]):String(row[column]??""))},{id:"copy-as",label:"复制为",icon:"lucide:clipboard-copy",children:copyAs},{id:"s1",separator:true},{id:"select-all",label:"全选",icon:"lucide:list-checks",action:()=>resultGridRef.value?.selectAll()},{id:"deselect",label:"取消全选",icon:"lucide:list-x",action:()=>resultGridRef.value?.deselectAll()},{id:"s2",separator:true},{id:"width",label:"设置当前列宽…",icon:"lucide:move-horizontal",action:async()=>{const value=Number(await promptValue("列宽（24-600 像素）","180"));if(value)resultGridRef.value?.setWidth(column,value)}},{id:"fit",label:"自动调整全部列宽",icon:"lucide:scan",action:()=>resultGridRef.value?.autoFitAll()},{id:"height",label:"设置行高…",icon:"lucide:move-vertical",action:async()=>{const value=Number(await promptValue("行高（24-120 像素）","30"));if(value)resultGridRef.value?.setRowHeight(value)}},{id:"s3",separator:true},{id:"freeze",label:"冻结到当前列",icon:"lucide:pin",action:()=>resultGridRef.value?.freeze(column)},{id:"unfreeze",label:"取消冻结所有列",icon:"lucide:pin-off",action:()=>resultGridRef.value?.unfreeze()},{id:"s4",separator:true},{id:"jump",label:"跳转到记录…",icon:"lucide:locate-fixed",action:async()=>{const target=Number(await promptValue("记录号",String(rowIndex+1)));if(target)resultGridRef.value?.goToRow(target-1)}}]};
}
function dataResultMenu(event:MouseEvent,rowIndex:number,column:string,tab?:DataTab){const data=tab?.data;if(!tab||!data)return;resultMenu(event,rowIndex,column,{index:0,kind:"rows",columns:data.columns.map(item=>({name:item.name,type:item.data_type,nullable:item.nullable})),rows:data.rows,row_count:data.rows.length},{database:tab.database,schema:tab.schema,table:tab.table,primaryKey:data.primary_key})}

function openWizard(state:WizardState){wizard.value=state;wizardStep.value=1;wizardFormat.value="csv";wizardCount.value=100;wizardIncludeData.value=true;wizardFile.value=undefined;wizardRunning.value=false;activeJobId.value="";jobCancelling.value=false;jobProgress.value={value:0,message:""};wizardPreview.value=undefined;wizardMapping.value={};wizardError.value='';wizardDelimiter.value=',';wizardEncoding.value='utf-8-sig';wizardNullMarker.value='\\N';wizardDefinitions.value=[];wizardColumns.value=[];wizardIncludeStructure.value=false;
  if(state.kind==='export'&&state.table&&profile.value){const target=wizard.value;void api<TableSchema>(`/schema/${profile.value.id}/${encodeURIComponent(state.database)}/${encodeURIComponent(state.table)}?schema=${encodeURIComponent(state.schema)}`).then(definition=>{if(wizard.value!==target)return;wizardDefinitions.value=definition.columns;wizardColumns.value=definition.columns.map(column=>column.name);}).catch(reason=>wizardError.value=errorText(reason));}
}
function upsertJob(job:BackgroundJob){const index=backgroundJobs.value.findIndex(item=>item.id===job.id);if(index>=0)backgroundJobs.value.splice(index,1,job);else backgroundJobs.value.unshift(job)}
async function refreshJobs(showError=false){try{backgroundJobs.value=await api<BackgroundJob[]>("/jobs?limit=50")}catch(reason){if(showError)flashError(reason)}}
function jobTitle(job:BackgroundJob){const filename=String(job.metadata.filename||"");if(filename)return filename;return job.kind==="generate"?"生成测试数据":job.kind==="import"?"数据导入":"后台任务"}
function jobTarget(job:BackgroundJob){return [job.metadata.database,job.metadata.schema,job.metadata.table].filter(Boolean).join(".")||"—"}
function jobStatus(job:BackgroundJob){return {queued:"等待中",running:"执行中",cancelling:"取消中",completed:"已完成",failed:"失败",cancelled:"已取消"}[job.status]}
function jobSize(job:BackgroundJob){const size=Number(job.metadata.bytes||0);return size?`${(size/1024/1024).toFixed(size>=10?1:2)} MB`:""}
async function runBackground(path:string,init:RequestInit){jobProgress.value={value:2,message:"正在上传文件并创建后台任务…"};const created=await api<BackgroundJob>(path,init);activeJobId.value=created.id;upsertJob(created);try{for(;;){const job=await api<BackgroundJob>(`/jobs/${created.id}`);upsertJob(job);if(activeJobId.value===job.id)jobProgress.value={value:job.progress,message:job.message};if(job.status==="completed")return job.result;if(job.status==="failed")throw new Error(job.error||"任务失败");if(job.status==="cancelled")throw new Error("任务已取消");await new Promise(resolve=>setTimeout(resolve,350))}}finally{if(activeJobId.value===created.id){activeJobId.value="";jobCancelling.value=false}void refreshJobs()}}
async function cancelJob(){if(!activeJobId.value||jobCancelling.value)return;jobCancelling.value=true;jobProgress.value={...jobProgress.value,message:"正在取消，等待数据库操作停止…"};try{await api(`/jobs/${activeJobId.value}/cancel`,{method:"POST"})}catch(reason){jobCancelling.value=false;flashError(reason)}}
async function cancelBackgroundJob(job:BackgroundJob){if(!["queued","running"].includes(job.status))return;try{upsertJob(await api<BackgroundJob>(`/jobs/${job.id}/cancel`,{method:"POST"}));}catch(reason){flashError(reason)}}
function sendWizardToBackground(){if(!wizardRunning.value)return;wizard.value=undefined;jobsOpen.value=true;flash("任务已转到后台，可在后台任务中查看进度")}
async function finishWizard(){if(!profile.value||!wizard.value)return;const target=wizard.value;wizardRunning.value=true;jobCancelling.value=false;try{let success="操作完成";
  const targetSchema=`&schema=${encodeURIComponent(target.schema)}`;
  if(target.kind==="export"&&target.table){if(wizardDefinitions.value.length&&!wizardColumns.value.length)throw new Error('请至少选择一个导出字段');const options=new URLSearchParams({columns:JSON.stringify(wizardColumns.value),delimiter:wizardDelimiter.value,null_marker:wizardNullMarker.value});const url=wizardFormat.value==='sql'&&wizardIncludeStructure.value?`${API_BASE}/sql/dump?profile_id=${profile.value.id}&database=${encodeURIComponent(target.database)}&table=${encodeURIComponent(target.table)}${targetSchema}&include_data=true`:`${API_BASE}/data/export?profile_id=${profile.value.id}&database=${encodeURIComponent(target.database)}&table=${encodeURIComponent(target.table)}${targetSchema}&format=${wizardFormat.value}&${options}`;await download(url,`${target.table}.${wizardFormat.value}`);success=`已导出 ${wizardFormat.value.toUpperCase()}`}
  else if(target.kind==="dump"){await download(`${API_BASE}/sql/dump?profile_id=${profile.value.id}&database=${encodeURIComponent(target.database)}&table=${encodeURIComponent(target.table||"")}${targetSchema}&include_data=${wizardIncludeData.value}`,`${target.table||target.database}.sql`);success="SQL 转储已生成"}
  else if(target.kind==="generate"&&target.table){const result=await runBackground("/jobs/generate",{method:"POST",body:jsonBody({profile_id:profile.value.id,database:target.database,schema:target.schema,table:target.table,count:wizardCount.value})}) as {affected_rows?:number};success=`已生成 ${result?.affected_rows||wizardCount.value} 行数据`}
  else if((target.kind==="import"||target.kind==="sql")&&wizardFile.value){const form=new FormData();form.append("file",wizardFile.value);const sqlFile=target.kind==="sql"||/\.sql$/i.test(wizardFile.value.name);if(target.kind==="import"&&target.table&&!sqlFile){const result=await runBackground(`/jobs/import?profile_id=${profile.value.id}&database=${encodeURIComponent(target.database)}&table=${encodeURIComponent(target.table)}${targetSchema}&${importOptions()}`,{method:"POST",body:form}) as {affected_rows?:number};success=`成功导入 ${result?.affected_rows||0} 行`}else{const result=await runBackground(`/jobs/sql-file?profile_id=${profile.value.id}&database=${encodeURIComponent(target.database)}${targetSchema}`,{method:"POST",body:form}) as {statements?:number};success=`SQL 文件执行完成：${result?.statements||0} 条语句`}}
  await loadObjects(target.database,target.schema);const dataTab=tabs.value.find(item=>item.type==="data"&&item.profileId===profile.value?.id&&item.database===target.database&&item.schema===target.schema&&item.table===target.table);if(dataTab?.type==="data")await loadData(dataTab);if(wizard.value===target)wizard.value=undefined;flash(success)
}catch(reason){if(errorText(reason)==="任务已取消")flash("任务已取消");else {wizardError.value=errorText(reason);flashError(reason)}}finally{if(wizard.value===target||!activeJobId.value){wizardRunning.value=false;jobCancelling.value=false}}}

async function testCurrentConnection() {
  const current = profile.value;
  if (!current) return;
  if (needsSshHostKey(current)) { openEditConnection(current); await testConnectionDialog(); return; }
  try {
    connectionState.value = "checking";
    const result = await api<{latency_ms:number}>(`/connections/${current.id}/test`, { method:"POST" });
    if (profile.value?.id !== current.id) return;
    markBackendOnline();
    const disconnected = active.value && !isDatabaseTab(active.value) && active.value.state !== "online";
    flash(`${driverLabel(current.driver)} 测试连接成功 · ${result.latency_ms} ms${disconnected ? '；当前会话已断开，请点击“重新连接”' : ''}`);
  } catch (reason) {
    if (profile.value?.id === current.id) { connectionState.value = "offline"; flashError(reason); }
  }
}
function openRemoteSession(driver: ConnectionDriver = profile.value?.driver || "ssh", profileId = profile.value?.id) {
  if (!isRemoteDriver(driver) || !profileId) return;
  const target = profiles.value.find(item => item.id === profileId);
  if (target && needsSshHostKey(target)) { openEditConnection(target); void testConnectionDialog(true); return; }
  const number = Math.max(0, ...tabs.value.filter(item => item.type === driver && item.profileId === profileId).map(item => (item as RemoteTab).sessionNumber || 1)) + 1;
  const title = driver === "k8s" ? `K8s 工作区 ${number}` : `${target?.name || driverLabel(driver)}${number > 1 ? ` (${number})` : ""}`;
  const tab: RemoteTab = { id: uid(), profileId, type: driver, title, sessionNumber: number, database: "", schema: "" };
  tabs.value.push(tab); activateTab(tab);
}
function acceptRemoteState(tab:RemoteTab,value:"online"|"checking"|"offline"){tab.state=value;if(tab.profileId===profile.value?.id&&tab.id===activeId.value)connectionState.value=value;}
function toolbarItems(): ToolbarItem[] {
  const create: ToolbarItem = { label:"新建连接", icon:"lucide:server-crash", action:()=>openCreateConnection() };
  const current = profile.value;
  if (!current) return [create];
  const pg = current.driver === "postgresql";
  const ms = current.driver === "mssql";
  const actions: Record<ToolbarCapability, ToolbarItem> = {
    database: { label:"新建数据库", icon:"lucide:database-backup", action:openCreateDatabase },
    query: { label:"新建查询", icon:"lucide:square-terminal", action:()=>newQuery() },
    table: { label:"新建表", icon:"lucide:table-properties", action:()=>openDesigner() },
    view: { label:"视图", icon:"lucide:panels-top-left", secondary:true, action:()=>newQuery(database.value,ms?"CREATE OR ALTER VIEW [dbo].[view_name] AS\nSELECT 1 AS [value];":pg?"CREATE OR REPLACE VIEW view_name AS\nSELECT 1 AS value;":"CREATE OR REPLACE VIEW view_name AS\nSELECT 1 AS value;",schema.value) },
    function: { label:"函数", icon:"lucide:function-square", secondary:true, action:()=>newQuery(database.value,ms?"CREATE OR ALTER FUNCTION [dbo].[function_name]() RETURNS int AS\nBEGIN\n  RETURN 1;\nEND;":pg?"CREATE FUNCTION function_name() RETURNS integer\nLANGUAGE sql AS $$ SELECT 1 $$;":"CREATE FUNCTION function_name() RETURNS INT DETERMINISTIC RETURN 1;",schema.value) },
    trigger: { label:"触发器", icon:"lucide:zap", secondary:true, action:()=>newQuery(database.value,ms?"CREATE OR ALTER TRIGGER [dbo].[trigger_name] ON [dbo].[table_name]\nAFTER INSERT AS\nBEGIN\n  SET NOCOUNT ON;\nEND;":pg?"CREATE FUNCTION trigger_function() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n  RETURN NEW;\nEND $$;\n\nCREATE TRIGGER trigger_name BEFORE INSERT ON table_name\nFOR EACH ROW EXECUTE FUNCTION trigger_function();":"CREATE TRIGGER trigger_name BEFORE INSERT ON table_name FOR EACH ROW SET NEW.created_at = NOW();",schema.value) },
    event: { label:"事件", icon:"lucide:clock-3", secondary:true, action:()=>newQuery(database.value,"CREATE EVENT event_name ON SCHEDULE EVERY 1 DAY DO SELECT 1;",schema.value) },
    privileges: { label:"用户权限", icon:"lucide:user-round-cog", action:()=>showPrivileges(database.value,"",schema.value) },
    backup: { label:"备份", icon:"lucide:archive-restore", action:()=>openWizard({kind:"dump",database:database.value,schema:schema.value}) },
    session: { label:current.driver==="ssh"?"新建终端":current.driver==="k8s"?"新建 K8s 工作区":"新建桌面", icon:current.driver==="ssh"?"lucide:square-terminal":current.driver==="k8s"?"lucide:ship-wheel":"lucide:monitor-up", action:()=>openRemoteSession(current.driver) },
    test: { label:"测试连接", icon:"lucide:plug-zap", action:testCurrentConnection },
    settings: { label:"连接设置", icon:"lucide:settings-2", action:()=>openEditConnection() },
  };
  return [create, ...toolbarCapabilitiesFor(current.driver).map(capability => actions[capability])];
}
</script>

<template>
  <main v-if="!authReady" class="loading-screen"><BrandMark /><span>正在恢复工作区…</span></main>
  <main v-else-if="!user" class="login-screen branded-login">
    <section class="login-story" aria-label="DeeBee 连接工作台">
      <div class="login-wordmark"><BrandMark /><strong>DeeBee<span>连接，让工作更简单。</span></strong></div>
      <div class="login-story-content">
        <h2>连接每一处，<br />掌控<span>每一步。</span></h2>
        <p>从数据库到远程服务器，<br />把日常运维，收进一个工作台。</p>
        <div class="connection-map" aria-hidden="true">
          <svg class="connection-map-lines" viewBox="0 0 460 230" fill="none"><path d="M115 45h67q18 0 18 18v104q0 18-18 18h-67m230-140h-67q-18 0-18 18v104q0 18 18 18h67M200 115h60" stroke="currentColor" stroke-width="1.5" /><circle cx="200" cy="115" r="4" fill="#F8BE53" /><circle cx="260" cy="115" r="4" fill="#F8BE53" /></svg>
          <div class="map-node map-database"><Icon icon="lucide:database" /><span>Database<small>查询与数据管理</small></span><i /></div>
          <div class="map-node map-terminal"><Icon icon="lucide:square-terminal" /><span>SSH<small>交互式远程终端</small></span><i /></div>
          <div class="map-hub"><BrandMark /></div>
          <div class="map-node map-cluster"><Icon icon="lucide:ship-wheel" /><span>Kubernetes<small>集群与容器管理</small></span><i /></div>
          <div class="map-node map-desktop"><Icon icon="lucide:monitor" /><span>Desktop<small>浏览器内远程桌面</small></span><i /></div>
        </div>
      </div>
      <footer class="login-story-footer">一个入口，多种可能。</footer>
    </section>
    <section class="login-form-panel">
      <span class="login-panel-caption"><Icon icon="lucide:panels-top-left" /> 管理员工作台</span>
      <a-form :model="{loginUser,loginPassword}" class="login-card branded-login-card" layout="vertical" @submit-success="login">
        <BrandMark />
        <h1>欢迎回来</h1>
        <p>登录工作台，继续你的工作。</p>
        <label for="login-username">用户名</label>
        <div class="login-input"><Icon icon="lucide:user-round" /><WorkbenchInput id="login-username" v-model="loginUser" aria-label="用户名" autocomplete="username" placeholder="输入用户名" required /></div>
        <label for="login-password">密码</label>
        <div class="login-input"><Icon icon="lucide:lock-keyhole" /><WorkbenchInput id="login-password" v-model="loginPassword" aria-label="密码" :type="loginPasswordVisible ? 'text' : 'password'" autocomplete="current-password" placeholder="输入密码" required /><IconButton html-type="button" :label="loginPasswordVisible ? '隐藏密码' : '显示密码'" :aria-pressed="loginPasswordVisible" @click="loginPasswordVisible=!loginPasswordVisible"><Icon :icon="loginPasswordVisible ? 'lucide:eye-off' : 'lucide:eye'" /></IconButton></div>
        <a-alert v-if="loginError" type="error" role="alert">{{ loginError }}</a-alert>
        <a-button type="primary" html-type="submit" class="login-button branded-login-button" :disabled="loginLoading"><span>{{ loginLoading ? "正在登录…" : "登录工作台" }}</span><Icon :icon="loginLoading ? 'lucide:loader-circle' : 'lucide:arrow-right'" :class="{spin:loginLoading}" /></a-button>
        <div class="login-form-note"><Icon icon="lucide:shield-check" /> 连接配置与凭据加密保存</div>
      </a-form>
      <footer class="login-panel-footer">DeeBee <span>·</span> 数据库与远程连接工作台</footer>
    </section>
  </main>
  <main v-else-if="fatal" class="fatal"><section><Icon icon="lucide:circle-alert" /><h2>无法打开工作台</h2><p>{{ fatal }}</p><a-button type="text" @click="fatal='';bootstrap()">重试</a-button><a-button type="text" @click="logout()">退出登录</a-button></section></main>
  <main v-else class="app-shell">
    <section class="toolbar" aria-label="上下文工具栏">
      <div class="workbench-brand" title="DeeBee 连接工作台"><BrandMark /><strong>DeeBee</strong></div>
      <a-button v-for="item in toolbarItems()" :key="item.label" type="text" :class="{'toolbar-secondary':item.secondary}" @click="item.action"><Icon :icon="item.icon" /><span>{{ item.label }}</span></a-button><div v-if="active?.type==='query'" class="toolbar-divider" />
      <a-button v-if="active?.type==='query'" type="text" :disabled="active.loading" @click="runQuery(active)"><Icon icon="lucide:play" class="green" /><span>运行</span></a-button><a-button v-if="active?.type==='query'" type="text" :disabled="!active.loading" @click="cancelQuery(active)"><Icon icon="lucide:square" /><span>停止</span></a-button><a-button v-if="active?.type==='query'" type="text" :disabled="active.autocommit||active.loading" @click="transaction(active,'commit')"><Icon icon="lucide:git-commit-horizontal" /><span>提交</span></a-button><a-button v-if="active?.type==='query'" type="text" :disabled="active.autocommit||active.loading" @click="transaction(active,'rollback')"><Icon icon="lucide:undo-2" /><span>回滚</span></a-button>
      <span class="toolbar-spacer" /><a-button type="text" class="jobs-button" :class="{active:jobsOpen}" aria-label="后台任务" @click="jobsOpen=!jobsOpen"><Icon icon="lucide:list-checks" /><span>后台任务</span><b v-if="runningJobCount">{{ runningJobCount }}</b></a-button><div v-if="profiles.length>1" class="form-field profile-switch"><Icon icon="lucide:server-cog" /><WorkbenchSelect :value="profile?.id" aria-label="当前连接" @change="switchProfile(($event.target as HTMLSelectElement).value)"><a-option v-for="item in profiles" :key="item.id" :value="item.id">{{ item.name }} · {{ driverLabel(item.driver) }}</a-option></WorkbenchSelect></div>
      <a-button v-if="profile" type="text" class="connection-state" :class="connectionState" :title="connectionState==='online'?'连接正常；点击检测连接':'点击立即重新连接'" @click="testCurrentConnection"><i /><span>{{ profile.name }} · {{ driverLabel(profile.driver) }}<template v-if="profile.user"> · {{ profile.user }}</template></span><b>{{ connectionLabel }}</b></a-button><a-button type="text" class="avatar" title="退出登录" @click="logout()">{{ user.slice(0,2).toUpperCase() }}</a-button>
    </section>
    <a-split class="workspace" component="section" :size="`${explorerWidth+3}px`" min="223px" :max="`${explorerMaxWidth+3}px`" @update:size="setExplorerSize">
      <template #first>
        <aside class="explorer">
          <header><strong>连接</strong><div><IconButton label="新建服务器连接" title="新建连接" @click="openCreateConnection()"><Icon icon="lucide:plus" /></IconButton><IconButton label="刷新服务器" title="刷新当前连接" :disabled="!profile" @click="refreshServer"><Icon icon="lucide:refresh-cw" /></IconButton></div></header><div v-if="profiles.length" class="form-field tree-search"><WorkbenchInput v-model="treeFilter" aria-label="筛选连接资源" placeholder="筛选连接、数据库或对象"><template #prefix><Icon icon="lucide:search" /></template></WorkbenchInput></div>
          <div v-if="profiles.length" class="tree connection-tree" role="tree" aria-label="连接资源树">
            <template v-for="item in visibleProfiles" :key="item.id">
              <a-button type="text" class="tree-node server-node profile-node" :class="{active:item.id===(selectedTreeProfileId||profile?.id)}" role="treeitem" :aria-label="`${!isDirectRemoteDriver(item.driver)&&item.id===profile?.id&&serverExpanded?'收起':'打开'}连接 ${item.name}`" :aria-expanded="isDirectRemoteDriver(item.driver)?undefined:item.id===profile?.id&&serverExpanded" @click="selectProfileNode(item)" @dblclick="connectProfileNode(item)" @keydown.enter="connectProfileNodeByKeyboard($event,item)" @contextmenu.prevent="connectionMenu($event,item)">
                <span class="tree-indent" /><span v-if="isDirectRemoteDriver(item.driver)" class="twisty placeholder" /><span v-else class="twisty" @click.stop="item.id===profile?.id?serverExpanded=!serverExpanded:switchProfile(item.id)"><Icon :icon="item.id===profile?.id&&serverExpanded?'lucide:chevron-down':'lucide:chevron-right'" /></span><Icon :icon="driverMetadata[item.driver].icon" class="node-icon server-icon" />
                <span class="node-label"><b>{{ item.name }}</b><small>{{ item.host }}:{{ item.port }}</small></span><span class="server-driver">{{ driverLabel(item.driver) }}</span>
              </a-button>
              <DatabaseTree v-if="item.id===profile?.id && isRelational && serverExpanded" rootless :profile="profile" :server-expanded="serverExpanded" :databases="databases" :schemas="schemas" :selected="database" :selected-schema="schema" :expanded="expanded" :expanded-schemas="expandedSchemas" :objects="objects" :filter="treeFilter" @toggle-server="serverExpanded=!serverExpanded" @server-menu="serverMenu" @select-database="chooseDatabase" @toggle-database="toggleDatabase" @select-schema="chooseSchema" @toggle-schema="toggleSchema" @open-table="openData" @design-table="openDesigner" @open-list="openList" @open-object="openObject" @database-menu="databaseMenu" @category-menu="categoryMenu" @table-menu="tableMenu" @object-menu="objectMenu" @inspect="inspect" />
              <template v-else-if="item.id===profile?.id && item.driver==='k8s' && serverExpanded">
                <div class="tree-node category-node remote-category-node" role="treeitem" aria-expanded="true"><span class="tree-indent level-one" /><span class="twisty placeholder" /><Icon icon="lucide:ship-wheel" class="node-icon" /><span class="node-label">Kubernetes 工作区</span><span class="node-count">{{ activeRemoteTabs.length }}</span></div>
                <a-button v-for="remoteTab in activeRemoteTabs" :key="remoteTab.id" type="text" class="tree-node object-node remote-session-node" :class="{active:activeId===remoteTab.id}" role="treeitem" @click="selectTab(remoteTab)"><span class="tree-indent level-two" /><span class="twisty placeholder" /><Icon :icon="remoteTab.type==='ssh'?'lucide:square-terminal':remoteTab.type==='k8s'?'lucide:ship-wheel':'lucide:monitor-up'" class="node-icon" /><span class="node-label">{{ remoteTab.title }}</span></a-button>
              </template>
            </template>
          </div>
          <div v-else class="empty-connections"><Icon icon="lucide:server-off" /><b>还没有服务器连接</b><span>添加数据库或远程服务器后即可开始。</span><a-button type="primary" class="primary" @click="openCreateConnection()"><Icon icon="lucide:plus" />新建连接</a-button></div>
          <footer><span>{{ profiles.length }} 个连接<template v-if="isRelational"> · {{ databases.length }} 个数据库</template><template v-else-if="profile&&isRemoteDriver(profile.driver)"> · {{ activeRemoteTabs.length }} 个会话</template></span><span :class="`connection-${connectionState}`">{{ connectionLabel }}</span></footer>
        </aside>
      </template>
      <template #resize-trigger><div class="explorer-divider" role="separator" aria-label="调整连接栏宽度" aria-orientation="vertical" :aria-valuemin="220" :aria-valuemax="explorerMaxWidth" :aria-valuenow="explorerWidth" tabindex="0" title="拖动调整连接栏宽度，双击恢复默认" @keydown.left.prevent="setExplorerWidth(explorerWidth-16)" @keydown.right.prevent="setExplorerWidth(explorerWidth+16)" @keydown.home.prevent="setExplorerWidth(220)" @keydown.end.prevent="setExplorerWidth(explorerMaxWidth)" @dblclick="setExplorerWidth(276)"><span /></div></template>
      <template #second>
        <section class="content">
          <WorkbenchTabs class="workspace-tabs" :active-key="activeId" type="card-gutter" editable @contextmenu.prevent="workspaceTabContext" @change="selectTabKey" @delete="closeTabKey">
            <a-tab-pane v-for="tab in tabs" :key="tab.id" closable>
              <template #title><span class="workspace-tab-label" :title="`${tabTitle(tab)} · ${profileNameForTab(tab)}`"><Icon :icon="tab.type==='query'||tab.type==='ssh'?'lucide:square-terminal':tab.type==='k8s'?'lucide:ship-wheel':tab.type==='rdp'?'lucide:monitor-up':tab.type==='data'?'lucide:table-2':tab.type==='designer'?'lucide:panel-top-open':'lucide:folder-tree'" /><span>{{ tabTitle(tab) }}</span><a-badge v-if="queryDirty(tab)" status="warning" /></span></template>
            </a-tab-pane>
            <template #extra><IconButton v-if="isRelational" label="新建查询标签" @click="newQuery()"><Icon icon="lucide:plus" /></IconButton><IconButton v-else-if="profile&&isRemoteDriver(profile.driver)" label="新建远程会话" @click="openRemoteSession(profile.driver)"><Icon icon="lucide:plus" /></IconButton></template>
          </WorkbenchTabs>
          <SshTerminal v-for="tab in sshTabs" v-show="activeId===tab.id" :key="tab.id" :profile-id="tab.profileId" :title="tabTitle(tab)" :active="activeId===tab.id" @state="acceptRemoteState(tab,$event)" />
          <RdpDesktop v-for="tab in rdpTabs" v-show="activeId===tab.id" :key="tab.id" :profile-id="tab.profileId" :title="tabTitle(tab)" :active="activeId===tab.id" @state="acceptRemoteState(tab,$event)" />
          <KubernetesWorkspace v-for="tab in k8sTabs" v-show="activeId===tab.id" :key="tab.id" :profile-id="tab.profileId" :title="tabTitle(tab)" :active="activeId===tab.id" @state="acceptRemoteState(tab,$event)" />
          <section v-if="profile && !isRelational && (!isRemoteDriver(profile.driver)||!active)" class="empty-workspace connection-phase"><Icon :icon="driverMetadata[profile.driver].icon" /><h2>{{ driverLabel(profile.driver) }} 连接已就绪</h2><div><a-button v-if="isRemoteDriver(profile.driver)" type="primary" class="primary" @click="openRemoteSession(profile.driver)"><Icon :icon="profile.driver==='ssh'?'lucide:square-terminal':profile.driver==='k8s'?'lucide:ship-wheel':'lucide:monitor-up'" />打开会话</a-button><a-button v-else type="primary" class="primary" @click="testCurrentConnection"><Icon icon="lucide:plug-zap" />重新测试</a-button><a-button type="text" @click="openEditConnection()"><Icon icon="lucide:settings-2" />连接设置</a-button></div></section>
          <section v-else-if="!active" class="empty-workspace branded-empty"><BrandMark /><h2>让工作，从连接开始。</h2><p>{{ profile ? '运行 SQL、浏览数据，或维护表结构。' : '添加数据库或远程服务器，开启你的第一个工作区。' }}</p><div v-if="profile"><a-button type="primary" class="primary" @click="newQuery()">新建查询</a-button><a-button type="text" @click="openDesigner()">新建表</a-button></div><a-button v-else type="primary" class="primary" @click="openCreateConnection()">新建连接</a-button></section>
          <section v-else-if="active.type==='query'" ref="queryViewRef" class="view query-view">
            <div class="query-bar"><a-button type="text" class="run-button" :disabled="active.loading" @click="runQuery(active)"><Icon icon="lucide:play" />运行</a-button><a-button type="text" :disabled="!active.loading" @click="cancelQuery(active)"><Icon icon="lucide:square" />停止</a-button><a-button type="text" @click="editorRef?.formatSql()"><Icon icon="lucide:wand-sparkles" />格式化</a-button><a-button type="text" :disabled="active.loading" @click="runQuery(active,`EXPLAIN ${active.sql}`)"><Icon icon="lucide:workflow" />执行计划</a-button><span class="bar-divider" /><a-button type="text" :disabled="active.autocommit||active.loading" @click="transaction(active,'commit')">提交</a-button><a-button type="text" :disabled="active.autocommit||active.loading" @click="transaction(active,'rollback')">回滚</a-button><div class="form-field autocommit-control"><a-switch :model-value="active.autocommit" :disabled="active.loading" aria-label="自动提交" @change="toggleAutocommit(active)" />自动提交</div><div class="form-field database-switch"><Icon icon="lucide:database" />数据库<WorkbenchSelect :value="active.database" aria-label="查询数据库" :disabled="active.loading" @change="switchQueryDatabase(active,($event.target as HTMLSelectElement).value)"><a-option v-for="db in databases" :key="db.name" :value="db.name">{{ db.name }}</a-option></WorkbenchSelect></div><div v-if="isSchemaDriver" class="form-field database-switch"><Icon icon="lucide:boxes" />Schema<WorkbenchSelect :value="active.schema" aria-label="查询 Schema" :disabled="active.loading" @change="switchQuerySchema(active,($event.target as HTMLSelectElement).value)"><a-option v-for="item in schemas[active.database]||[]" :key="item.name" :value="item.name">{{ item.name }}</a-option></WorkbenchSelect></div><a-button type="text" @click="historyOpen=!historyOpen"><Icon icon="lucide:history" />历史</a-button></div>
            <div class="editor-wrap" :style="{height:`${queryEditorHeight}px`}"><SqlEditor :context-id="active.profileId+active.id+active.database+active.schema" ref="editorRef" v-model="active.sql" :catalog="catalogs[contextKey(active.database,active.schema)]" :dialect="isPostgres?'postgresql':isMssql?'mssql':'mysql'" @run="runQuery(active,$event)" /><aside v-if="historyOpen" class="history-panel"><header><strong>查询与历史</strong><IconButton label="关闭" @click="historyOpen=false"><Icon icon="lucide:x" /></IconButton></header><h4>已保存查询</h4><a-button v-for="item in savedQueries" :key="item.name" type="text" @click="useSavedQuery(item)"><b>{{ item.name }}</b><code>{{ item.sql }}</code></a-button><h4>最近执行</h4><a-button v-for="(sql,index) in history" :key="index" type="text" @click="active.sql=sql;historyOpen=false"><code>{{ sql }}</code></a-button></aside></div>
            <div class="query-splitter" role="separator" aria-label="调整 SQL 编辑区高度" aria-orientation="horizontal" :aria-valuemin="120" :aria-valuemax="editorHeightLimit()" :aria-valuenow="queryEditorHeight" tabindex="0" title="拖动调整 SQL 编辑区高度，双击恢复默认" @pointerdown="startEditorResize" @keydown="resizeEditorByKeyboard" @dblclick="setEditorHeight(260)"><span><i /><i /><i /></span></div>
            <WorkbenchTabs class="result-tabs component-tabs" :active-key="resultPane" size="small" @change="resultPane=String($event)"><a-tab-pane key="message" title="消息" /><a-tab-pane key="summary" title="摘要" /><template v-for="(result,index) in active.response?.results" :key="index"><a-tab-pane v-if="result.kind==='rows'" :key="`result-${index}`" :title="`结果 ${index+1} (${result.row_count})`" /></template><a-tab-pane key="profile" title="Profile" /><a-tab-pane key="status" title="状态" /></WorkbenchTabs>
            <div v-if="resultPane==='message'" class="query-message" :class="{error:active.error}"><Icon :icon="active.loading?'lucide:loader-circle':active.error?'lucide:circle-x':'lucide:circle-check'" :class="{spin:active.loading}" /><strong>{{ active.loading?'正在执行…':active.error?'执行失败':active.response?'执行完成':'准备就绪' }}</strong><pre v-if="active.error">{{ active.error }}</pre><p v-else-if="active.response">{{ active.response.results.length }} 个结果，耗时 {{ active.response.elapsed_ms }} ms</p><p v-else>SQL 编辑器支持当前上下文的表、视图、字段、别名和函数补全。</p></div>
            <div v-else-if="resultPane==='summary'" class="summary-pane"><div><b>{{ active.response?.elapsed_ms || 0 }} ms</b><span>执行时间</span></div><div><b>{{ active.response?.results.length || 0 }}</b><span>结果集</span></div><div><b>{{ active.response?.results.reduce((n,r)=>n+(r.row_count||r.affected_rows||0),0) || 0 }}</b><span>行数/影响行</span></div><div><b>{{ active.response?.warnings?.length || 0 }}</b><span>警告</span></div></div>
            <EditableGrid v-else-if="activeResult?.kind==='rows'" ref="resultGridRef" :columns="activeResult.columns || []" :rows="activeResult.rows || []" :primary-key="activeResult.source?.primaryKey || []" :editable="active.autocommit && Boolean(activeResult.source?.primaryKey?.length)" :save-rows="saveQueryEdits" :storage-key="`query:${active.database}:${active.schema}:${activeResult.source?.table || 'result'}`" @export="exportGrid" @save="saveActiveQueryInline" @menu="(event,row,column)=>resultMenu(event,row,column,activeResult!,activeResult!.source)" />
            <div v-else-if="resultPane==='profile'" class="query-message query-profile"><strong>执行概要</strong><p>结果数据请查看对应的“结果”页签。</p><pre>{{ queryProfileText }}</pre></div><div v-else-if="resultPane==='status'" class="query-message query-status"><strong>服务器状态</strong><pre>{{ queryStatusText }}</pre></div>
            <footer class="statusbar"><span>{{ active.loading?'正在执行':active.error?'查询失败':'就绪' }}</span><span>UTF-8 · {{ profile?driverLabel(profile.driver):'' }} · {{ active.database }}{{ active.schema?`.${active.schema}`:'' }}</span><span>{{ active.autocommit?'自动提交':'手动事务' }}</span></footer>
          </section>
          <section v-else-if="activeData" class="view data-view">
            <div class="data-toolbar"><a-button type="text" @click="rowDialog={mode:'insert',tabId:activeData.id,values:{},valueModes:Object.fromEntries((activeData.data?.columns||[]).map(column=>[column.name,'default']))}"><Icon icon="lucide:plus" />添加记录</a-button><a-button type="text" status="danger" :disabled="activeData.selected==null" class="danger-text" @click="deleteActiveRow"><Icon icon="lucide:trash-2" />删除</a-button><span class="separator" /><div class="form-field">筛选<WorkbenchSelect v-model="activeData.filterColumn" aria-label="筛选字段"><a-option value="">选择字段</a-option><a-option v-for="column in activeData.data?.columns" :key="column.name" :value="column.name">{{ column.name }}</a-option></WorkbenchSelect><WorkbenchInput v-model="activeData.filterValue" aria-label="筛选值" placeholder="包含…" /><a-button type="text" @click="reloadActiveData({page:1})">应用</a-button></div><span class="grow" /><a-button v-if="!isMssql" type="text" @click="openDesigner(activeData.table,activeData.database,activeData.schema)"><Icon icon="lucide:panel-top-open" />设计表</a-button><a-button type="text" @click="reloadActiveData()"><Icon icon="lucide:refresh-cw" />刷新</a-button><span class="separator" /><a-button type="text" @click="openWizard({kind:'import',database:activeData.database,schema:activeData.schema,table:activeData.table})"><Icon icon="lucide:file-input" />导入</a-button><a-button type="text" @click="openWizard({kind:'export',database:activeData.database,schema:activeData.schema,table:activeData.table})"><Icon icon="lucide:file-output" />导出</a-button></div>
            <div v-if="activeData.error" class="inline-error">{{ activeData.error }}</div><EditableGrid ref="resultGridRef" :columns="activeData.data?.columns || []" :rows="activeData.data?.rows || []" :primary-key="activeData.data?.primary_key || []" :editable="Boolean(activeData.data?.primary_key.length)" :save-rows="edits=>saveDataEdits(activeData!,edits)" :selected="activeData.selected" :storage-key="`${activeData.database}:${activeData.schema}:${activeData.table}`" @select="activeData.selected=$event" @sort="sortActiveData" @export="exportGrid" @save="saveActiveInline" @menu="(event,row,column)=>dataResultMenu(event,row,column,activeData)" /><footer class="data-footer"><span>第 {{ activeData.page }} 页，共 {{ activeData.data?.total || 0 }}{{ activeData.data?.limited ? '+' : '' }} 行<span v-if="activeData.data?.limited">（已限制为前 {{ TABLE_DATA_LIMIT }} 行）</span></span><a-pagination :current="activeData.page" :total="activeData.data?.total||0" :page-size="100" simple @change="reloadActiveData({page:$event})" /><span>主键：{{ activeData.data?.primary_key.join(', ') || '无' }}</span></footer>
          </section>
          <TableDesigner v-else-if="activeDesigner" :tab="activeDesigner" @update="updateSpec(activeDesigner,$event)" @pane="activeDesigner.pane=$event" @preview="previewDdl(activeDesigner)" @apply="applyDdl(activeDesigner)" />
          <ObjectList v-else-if="activeObjectsTab" :key="activeObjectsTab.id" :tab="activeObjectsTab" :objects="objects[contextKey(activeObjectsTab.database,activeObjectsTab.schema)]" @open-table="openData($event,activeObjectsTab.database,activeObjectsTab.schema)" @design-table="openDesigner($event,activeObjectsTab.database,activeObjectsTab.schema)" @open-object="openObject(activeObjectsTab.kind,$event,activeObjectsTab.database,activeObjectsTab.schema)" @menu="objectListMenu" />
        </section>
      </template>
    </a-split>
    <a-drawer :visible="!!inspector" title="对象信息" :width="360" :footer="false" :mask="false" :mask-closable="false" @cancel="inspector=undefined"><section v-if="inspector" class="inspector-details"><header><strong>对象信息</strong><IconButton label="关闭" @click="inspector=undefined"><Icon icon="lucide:x" /></IconButton></header><div class="inspector-icon"><Icon icon="lucide:database" /></div><h3>{{ inspector.title }}</h3><p>{{ inspector.subtitle }}</p><dl><div v-for="(line,index) in inspector.lines" :key="index"><dt>{{ index+1 }}</dt><dd>{{ line }}</dd></div></dl></section></a-drawer>
    <a-drawer :visible="jobsOpen" title="后台任务列表" :width="520" :footer="false" @cancel="jobsOpen=false">
      <section class="jobs-drawer-content">
        <header><div><Icon icon="lucide:list-checks" /><span><strong>后台任务</strong><small>{{ runningJobCount ? `${runningJobCount} 个正在执行` : '最近 50 个任务' }}</small></span></div><nav><IconButton label="刷新后台任务" @click="refreshJobs(true)"><Icon icon="lucide:refresh-cw" /></IconButton><IconButton label="关闭后台任务" @click="jobsOpen=false"><Icon icon="lucide:x" /></IconButton></nav></header>
        <div v-if="backgroundJobs.length" class="job-list"><article v-for="job in backgroundJobs" :key="job.id" :class="`job-${job.status}`"><div class="job-heading"><Icon :icon="job.kind==='sql-file'?'lucide:file-code-2':job.kind==='import'?'lucide:file-input':'lucide:sparkles'" /><span><b :title="jobTitle(job)">{{ jobTitle(job) }}</b><small>{{ jobTarget(job) }}<template v-if="jobSize(job)"> · {{ jobSize(job) }}</template></small></span><em>{{ jobStatus(job) }}</em></div><a-progress :percent="job.progress/100" :show-text="false" :status="job.status==='failed'?'danger':job.status==='completed'?'success':'normal'" /><p>{{ job.error || job.message }}<small>{{ new Date(job.created_at*1000).toLocaleString() }}</small></p><a-button v-if="job.status==='queued'||job.status==='running'" type="text" @click="cancelBackgroundJob(job)">取消</a-button></article></div>
        <a-empty v-else description="暂无后台任务" />
      </section>
    </a-drawer>
    <SshFileManager v-if="fileManagerProfile" :key="fileManagerProfile.id" :profile="fileManagerProfile" @close="fileManagerProfile=undefined" />
    <ContextMenu :menu="contextMenu" @close="closeMenu" />
    <WorkbenchModal v-if="connectionDialog" visible :width="820" :footer="false" hide-title :closable="false" :mask-closable="false" modal-class="workbench-dialog" @cancel="connectionDialog=undefined">
      <a-form :model="connectionDialog" layout="vertical" class="modal connection-modal" role="dialog" autocomplete="off" :aria-label="connectionDialog.mode==='edit'?'编辑服务器连接':'新建服务器连接'" @submit-success="saveConnection">
        <header><div class="connection-identity"><Icon :icon="driverMetadata[connectionDialog.driver].icon" /><span><strong>{{ driverMetadata[connectionDialog.driver].label }}</strong><small>{{ connectionDialog.mode==='edit'?'编辑连接':'新建连接' }}</small></span></div><IconButton html-type="button" label="关闭连接设置" @click="connectionDialog=undefined"><Icon icon="lucide:x" /></IconButton></header>
        <section>
          <div class="driver-picker"><a-button v-for="key in driverChoices" :key="key" type="text" html-type="button" :class="{active:connectionDialog.driver===key}" :disabled="connectionDialog.mode==='edit'" @click="setConnectionDriver(key)"><Icon :icon="driverMetadata[key].icon" /><b>{{ driverMetadata[key].label }}</b></a-button></div>
          <WorkbenchTabs class="connection-sections component-tabs" :active-key="connectionDialog.section" aria-label="连接设置分区" @change="connectionDialog.section=$event as 'general'|'options'|'ssh'|'proxy'"><a-tab-pane key="general" title="常规" /><a-tab-pane key="options" title="选项" /><a-tab-pane v-if="isDatabaseDriver(connectionDialog.driver)" key="ssh" title="SSH 隧道" /><a-tab-pane v-if="isDatabaseDriver(connectionDialog.driver)" key="proxy" title="代理" /></WorkbenchTabs>
          <div v-if="connectionDialog.error" class="connection-form-message error"><Icon icon="lucide:circle-x" />{{ connectionDialog.error }}</div><div v-else-if="connectionDialog.testMessage" class="connection-form-message success"><Icon icon="lucide:circle-check" />{{ connectionDialog.testMessage }}</div>
          <div v-if="connectionDialog.hostKey" class="ssh-host-key-confirmation" role="group" aria-label="确认 SSH 主机密钥" aria-live="polite">
            <strong>首次连接：确认 SSH 主机密钥</strong>
            <p>主机：{{ connectionDialog.hostKey.host }}:{{ connectionDialog.hostKey.port }}</p>
            <code>{{ connectionDialog.hostKey.fingerprint }}</code>
            <p>请通过可信渠道核对 SHA256 指纹。确认后将保存该指纹，后续主机密钥变化时会阻止连接。</p>
            <div><a-button type="text" html-type="button" @click="rejectConnectionHostKey">取消信任</a-button><a-button type="primary" html-type="button" class="primary" @click="confirmConnectionHostKey">{{ connectionDialog.hostKey.saveAfterConfirm ? '信任并保存连接' : '信任此主机' }}</a-button></div>
          </div>
          <p v-else-if="needsSshHostKey(connectionDialog)" class="ssh-host-key-hint">首次连接需要确认 SSH 主机密钥。点击“测试连接”或“保存并连接”后，将在此处显示指纹和确认按钮。</p>
          <div v-if="connectionDialog.section==='general'" class="connection-fields">
            <div class="form-field"><span>连接名称</span><WorkbenchInput v-model.trim="connectionDialog.name" aria-label="连接名称" autofocus required maxlength="100" placeholder="例如：生产库（只读）" @input="useCustomConnectionName" /></div>
            <div class="field-pair"><div class="form-field"><span>主机</span><WorkbenchInput v-model.trim="connectionDialog.host" aria-label="服务器主机" required maxlength="255" placeholder="127.0.0.1" /></div><div class="form-field port-field"><span>端口</span><WorkbenchInput v-model.number="connectionDialog.port" aria-label="服务器端口" type="number" min="1" max="65535" required /></div></div>
            <div v-if="connectionDialog.driver!=='k8s'" class="form-field"><span>用户名</span><WorkbenchInput v-model.trim="connectionDialog.user" name="connection-username" aria-label="连接用户名" autocomplete="off" :required="connectionDialog.driver==='mysql'||connectionDialog.driver==='postgresql'||connectionDialog.driver==='mssql'||connectionDialog.driver==='clickhouse'||connectionDialog.driver==='ssh'" maxlength="255" /></div>
            <div v-if="connectionDialog.driver!=='k8s'||connectionDialog.options.k8s_auth_method==='token'" class="form-field"><span>{{ connectionDialog.driver==='k8s'?'Bearer Token':connectionDialog.driver==='ssh'&&connectionDialog.options.auth_method==='private_key'?'私钥口令':'密码' }}</span><WorkbenchInput v-model="connectionDialog.password" name="connection-password" aria-label="连接密码" type="password" autocomplete="new-password" :placeholder="connectionDialog.mode==='edit'?'留空则保持原密钥':'可留空'" /></div>
            <div v-if="!isRemoteDriver(connectionDialog.driver)" class="form-field"><span>{{ connectionDialog.driver==='redis'?'逻辑数据库编号':'默认数据库' }}</span><WorkbenchInput v-model.trim="connectionDialog.default_database" aria-label="默认数据库" :type="connectionDialog.driver==='redis'?'number':'text'" :min="connectionDialog.driver==='redis'?0:undefined" :placeholder="connectionDialog.driver==='postgresql'?'postgres':connectionDialog.driver==='mssql'?'master':connectionDialog.driver==='clickhouse'?'default':connectionDialog.driver==='redis'?'0':'可选'" /></div>
            <div v-if="usesSchemas(connectionDialog.driver)" class="form-field"><span>默认 Schema</span><WorkbenchInput v-model.trim="connectionDialog.default_schema" aria-label="默认 Schema" :placeholder="connectionDialog.driver==='mssql'?'dbo':'public'" /></div>
            <div v-if="!isRemoteDriver(connectionDialog.driver)" class="form-field connection-string-row"><span>连接地址</span><code>{{ connectionStringPreview }}</code></div>
          </div>
          <div v-else-if="connectionDialog.section==='options'" class="connection-fields connection-option-fields">
            <template v-if="connectionDialog.driver==='mssql'"><div class="form-field"><span>传输加密</span><WorkbenchSelect v-model="connectionDialog.options.encryption" aria-label="SQL Server 传输加密"><a-option value="request">优先加密</a-option><a-option value="require">必须加密</a-option><a-option value="off">关闭加密</a-option></WorkbenchSelect></div><div class="connection-options"><WorkbenchCheckbox v-model="connectionDialog.options.read_only">只读应用意图</WorkbenchCheckbox></div></template>
            <div v-if="connectionDialog.driver==='mongodb'" class="form-field"><span>认证数据库</span><WorkbenchInput v-model.trim="connectionDialog.options.auth_database" aria-label="MongoDB 认证数据库" placeholder="admin" /></div>
            <div v-if="isConnectionOnlyDriver(connectionDialog.driver)" class="connection-options"><WorkbenchCheckbox v-model="connectionDialog.options.tls">启用 TLS</WorkbenchCheckbox><WorkbenchCheckbox v-if="connectionDialog.options.tls" v-model="connectionDialog.options.verify_tls">验证服务器证书</WorkbenchCheckbox><WorkbenchCheckbox v-if="connectionDialog.driver==='mongodb'" v-model="connectionDialog.options.direct_connection">直连单个 MongoDB 节点</WorkbenchCheckbox></div>
            <template v-if="connectionDialog.driver==='ssh'"><div class="form-field"><span>认证方式</span><WorkbenchSelect v-model="connectionDialog.options.auth_method" aria-label="SSH 认证方式"><a-option value="password">密码</a-option><a-option value="private_key">私钥</a-option></WorkbenchSelect></div><div v-if="connectionDialog.options.auth_method==='private_key'" class="form-field private-key-field"><span>SSH 私钥</span><WorkbenchInput v-model="connectionDialog.private_key" multiline aria-label="SSH 私钥" spellcheck="false" :placeholder="connectionDialog.mode==='edit'?'留空则保持原私钥':'-----BEGIN OPENSSH PRIVATE KEY-----'" /></div><div class="connection-options"><WorkbenchCheckbox v-model="connectionDialog.options.strict_host_key">验证 SSH 主机密钥</WorkbenchCheckbox></div><div v-if="connectionDialog.options.host_key_fingerprint" class="form-field"><span>主机密钥指纹</span><WorkbenchInput v-model="connectionDialog.options.host_key_fingerprint" aria-label="SSH 主机密钥指纹" readonly /></div></template>
            <template v-if="connectionDialog.driver==='rdp'"><div class="form-field"><span>Windows 域</span><WorkbenchInput v-model.trim="connectionDialog.options.domain" aria-label="Windows 域" placeholder="可选" /></div><div class="field-pair"><div class="form-field"><span>安全模式</span><WorkbenchSelect v-model="connectionDialog.options.security" aria-label="RDP 安全模式"><a-option value="any">自动协商</a-option><a-option value="nla">NLA</a-option><a-option value="tls">TLS</a-option><a-option value="rdp">RDP</a-option></WorkbenchSelect></div><div class="form-field short-field"><span>色深</span><WorkbenchSelect v-model.number="connectionDialog.options.color_depth" aria-label="RDP 色深"><a-option :value="16">16 位</a-option><a-option :value="24">24 位</a-option><a-option :value="32">32 位</a-option></WorkbenchSelect></div></div><div class="connection-options"><WorkbenchCheckbox v-model="connectionDialog.options.ignore_certificate">忽略 RDP 服务器证书错误</WorkbenchCheckbox></div></template>
            <template v-if="connectionDialog.driver==='k8s'"><div class="form-field"><span>认证方式</span><WorkbenchSelect v-model="connectionDialog.options.k8s_auth_method" aria-label="Kubernetes 认证方式"><a-option value="token">Bearer Token（JumpServer 兼容）</a-option><a-option value="kubeconfig">完整 Kubeconfig / 客户端证书</a-option></WorkbenchSelect></div><div class="form-field"><span>默认 Namespace</span><WorkbenchInput v-model.trim="connectionDialog.options.namespace" aria-label="Kubernetes 默认 Namespace" placeholder="留空则显示所有可访问命名空间" /></div><div v-if="connectionDialog.options.k8s_auth_method==='token'" class="connection-options"><WorkbenchCheckbox v-model="connectionDialog.options.verify_tls">验证 API Server TLS 证书</WorkbenchCheckbox></div><div v-else class="form-field private-key-field"><span>Kubeconfig</span><FilePicker accept=".yaml,.yml,.conf,.txt" label="选择 Kubeconfig 文件" @change="loadKubeconfigFile" /><WorkbenchInput v-model="connectionDialog.private_key" multiline aria-label="Kubeconfig 内容" spellcheck="false" :placeholder="connectionDialog.mode==='edit'?'留空则保持原 Kubeconfig':'apiVersion: v1\nclusters: …'" /></div><div class="connection-empty-options"><Icon icon="lucide:shield-check" /><strong>Kubeconfig 与客户端私钥加密保存，不发送到浏览器会话。</strong></div></template>
            <div v-if="connectionDialog.driver==='mysql'||connectionDialog.driver==='postgresql'" class="connection-empty-options"><Icon icon="lucide:badge-check" /><strong>使用推荐的驱动设置</strong></div>
          </div>
          <div v-else-if="connectionDialog.section==='ssh'" class="connection-fields connection-option-fields">
            <div class="connection-options connection-toggle"><WorkbenchCheckbox v-model="connectionDialog.options.ssh_tunnel">通过 SSH 隧道连接</WorkbenchCheckbox></div>
            <template v-if="connectionDialog.options.ssh_tunnel">
              <div class="field-pair"><div class="form-field"><span>SSH 主机</span><WorkbenchInput v-model.trim="connectionDialog.options.ssh_host" aria-label="SSH 隧道主机" required maxlength="255" @input="connectionDialog.options.ssh_host_key_fingerprint=''" /></div><div class="form-field port-field"><span>端口</span><WorkbenchInput v-model.number="connectionDialog.options.ssh_port" aria-label="SSH 隧道端口" type="number" min="1" max="65535" required @input="connectionDialog.options.ssh_host_key_fingerprint=''" /></div></div>
              <div class="form-field"><span>SSH 用户名</span><WorkbenchInput v-model.trim="connectionDialog.options.ssh_user" aria-label="SSH 隧道用户名" autocomplete="off" required maxlength="255" /></div>
              <div class="form-field"><span>认证方式</span><WorkbenchSelect v-model="connectionDialog.options.ssh_auth_method" aria-label="SSH 隧道认证方式"><a-option value="password">密码</a-option><a-option value="private_key">私钥</a-option></WorkbenchSelect></div>
              <div class="form-field"><span>{{ connectionDialog.options.ssh_auth_method==='private_key'?'私钥口令':'SSH 密码' }}</span><WorkbenchInput v-model="connectionDialog.ssh_password" aria-label="SSH 隧道密码" type="password" autocomplete="new-password" :placeholder="connectionDialog.mode==='edit'?'留空则保持原密码':'可留空'" /></div>
              <div v-if="connectionDialog.options.ssh_auth_method==='private_key'" class="form-field private-key-field"><span>SSH 私钥</span><WorkbenchInput v-model="connectionDialog.ssh_private_key" multiline aria-label="SSH 隧道私钥" spellcheck="false" :placeholder="connectionDialog.mode==='edit'?'留空则保持原私钥':'-----BEGIN OPENSSH PRIVATE KEY-----'" /></div>
              <div class="field-pair"><div class="form-field"><span>主机密钥</span><WorkbenchSelect v-model="connectionDialog.options.ssh_host_key_mode" aria-label="SSH 隧道主机密钥策略"><a-option value="accept_new">接受新主机</a-option><a-option value="strict">严格校验</a-option><a-option value="insecure">不校验</a-option></WorkbenchSelect></div><div class="form-field short-field"><span>保活</span><WorkbenchInput v-model.number="connectionDialog.options.ssh_keepalive_seconds" aria-label="SSH 隧道保活秒数" type="number" min="0" max="300" /></div></div>
              <div v-if="connectionDialog.options.ssh_host_key_fingerprint" class="form-field"><span>主机密钥指纹</span><WorkbenchInput v-model="connectionDialog.options.ssh_host_key_fingerprint" aria-label="SSH 隧道主机密钥指纹" /></div>
            </template>
          </div>
          <div v-else class="connection-fields connection-option-fields">
            <div class="connection-options connection-toggle"><WorkbenchCheckbox v-model="connectionDialog.options.proxy_enabled">通过代理服务器连接</WorkbenchCheckbox></div>
            <template v-if="connectionDialog.options.proxy_enabled">
              <div class="form-field"><span>代理类型</span><WorkbenchSelect v-model="connectionDialog.options.proxy_type" aria-label="代理类型"><a-option value="socks5">SOCKS5</a-option><a-option value="http">HTTP CONNECT</a-option></WorkbenchSelect></div>
              <div class="field-pair"><div class="form-field"><span>代理主机</span><WorkbenchInput v-model.trim="connectionDialog.options.proxy_host" aria-label="代理主机" required maxlength="255" /></div><div class="form-field port-field"><span>端口</span><WorkbenchInput v-model.number="connectionDialog.options.proxy_port" aria-label="代理端口" type="number" min="1" max="65535" required /></div></div>
              <div class="form-field"><span>代理用户名</span><WorkbenchInput v-model.trim="connectionDialog.options.proxy_user" aria-label="代理用户名" autocomplete="off" placeholder="可选" maxlength="255" /></div>
              <div class="form-field"><span>代理密码</span><WorkbenchInput v-model="connectionDialog.proxy_password" aria-label="代理密码" type="password" autocomplete="new-password" :placeholder="connectionDialog.mode==='edit'?'留空则保持原密码':'可选'" /></div>
              <div v-if="connectionDialog.options.proxy_type==='socks5'" class="connection-options"><WorkbenchCheckbox v-model="connectionDialog.options.proxy_dns">由代理解析目标主机</WorkbenchCheckbox></div>
            </template>
          </div>
        </section>
        <footer><a-button type="text" html-type="button" :disabled="connectionDialog.testing||connectionDialog.saving" @click="testConnectionDialog()"><Icon v-if="connectionDialog.testing" icon="lucide:loader-circle" class="spin" />{{ connectionDialog.testing?'正在测试…':'测试连接' }}</a-button><a-button type="text" html-type="button" @click="connectionDialog=undefined">取消</a-button><a-button type="primary" class="primary" html-type="submit" :disabled="connectionDialog.saving||connectionDialog.testing||!!connectionDialog.hostKey">{{ connectionDialog.saving?'正在保存…':'保存并连接' }}</a-button></footer>
      </a-form>
    </WorkbenchModal>
    <WorkbenchModal v-if="createDatabaseDialog" visible :width="620" :footer="false" hide-title :closable="false" :mask-closable="false" modal-class="workbench-dialog" @cancel="createDatabaseDialog=undefined"><a-form :model="createDatabaseDialog" layout="vertical" class="modal create-database-modal" role="dialog" aria-label="新建数据库" @submit-success="createDatabase"><header><div><Icon :icon="profile?driverMetadata[profile.driver].icon:'lucide:database-backup'" /><span><strong>新建数据库</strong><small>{{ profile ? driverLabel(profile.driver) : '' }}</small></span></div><IconButton html-type="button" label="关闭新建数据库" @click="createDatabaseDialog=undefined"><Icon icon="lucide:x" /></IconButton></header><section><div class="form-field"><span>数据库名称</span><WorkbenchInput v-model.trim="createDatabaseDialog.name" aria-label="数据库名称" autofocus autocomplete="off" placeholder="例如 app_data" pattern="[A-Za-z_][A-Za-z0-9_$]*" required /></div><template v-if="isMssql"><div class="form-field"><span>排序规则</span><WorkbenchSelect v-model="createDatabaseDialog.collation" aria-label="SQL Server 排序规则"><a-option value="">服务器默认</a-option><a-option value="Chinese_PRC_CI_AS">Chinese_PRC_CI_AS</a-option><a-option value="Latin1_General_100_CI_AS_SC_UTF8">Latin1_General_100_CI_AS_SC_UTF8</a-option><a-option value="SQL_Latin1_General_CP1_CI_AS">SQL_Latin1_General_CP1_CI_AS</a-option></WorkbenchSelect></div><div class="form-field"><span>恢复模式</span><WorkbenchSelect v-model="createDatabaseDialog.recovery_model" aria-label="SQL Server 恢复模式"><a-option value="SIMPLE">简单</a-option><a-option value="FULL">完整</a-option><a-option value="BULK_LOGGED">大容量日志</a-option></WorkbenchSelect></div><div class="database-size-fields"><div class="form-field"><span>数据文件</span><div><WorkbenchInput v-model.number="createDatabaseDialog.data_size_mb" type="number" min="1" max="1048576" aria-label="数据文件初始大小" required /><b>MB</b></div></div><div class="form-field"><span>日志文件</span><div><WorkbenchInput v-model.number="createDatabaseDialog.log_size_mb" type="number" min="1" max="1048576" aria-label="日志文件初始大小" required /><b>MB</b></div></div><div class="form-field"><span>自动增长</span><div><WorkbenchInput v-model.number="createDatabaseDialog.filegrowth_mb" type="number" min="1" max="1048576" aria-label="数据库文件自动增长量" required /><b>MB</b></div></div></div></template><template v-else><div class="form-field"><span>字符集</span><WorkbenchSelect v-model="createDatabaseDialog.charset" aria-label="数据库字符集" :disabled="isPostgres"><a-option v-if="isPostgres" value="UTF8">UTF8</a-option><template v-else><a-option value="utf8mb4">utf8mb4</a-option><a-option value="utf8">utf8</a-option><a-option value="latin1">latin1</a-option><a-option value="ascii">ascii</a-option></template></WorkbenchSelect></div><div v-if="!isPostgres" class="form-field"><span>排序规则</span><WorkbenchSelect v-model="createDatabaseDialog.collation" aria-label="数据库排序规则"><a-option value="utf8mb4_unicode_ci">utf8mb4_unicode_ci</a-option><a-option value="utf8mb4_general_ci">utf8mb4_general_ci</a-option><a-option value="utf8mb4_bin">utf8mb4_bin</a-option></WorkbenchSelect></div></template></section><footer><a-button type="text" html-type="button" @click="createDatabaseDialog=undefined">取消</a-button><a-button type="primary" class="primary" html-type="submit" :disabled="createDatabaseDialog.saving || !createDatabaseDialog.name">{{ createDatabaseDialog.saving ? '正在创建…' : '创建' }}</a-button></footer></a-form></WorkbenchModal>
    <WorkbenchModal v-if="rowDialog && activeData" visible :width="680" :footer="false" hide-title :closable="false" :mask-closable="false" modal-class="workbench-dialog" @cancel="rowDialog=undefined"><a-form :model="rowDialog" layout="vertical" class="modal row-modal" @submit-success="insertActiveRow"><header><div><Icon icon="lucide:plus" /><span><strong>添加记录</strong><small>{{ activeData.schema ? `${activeData.schema}.` : '' }}{{ activeData.table }}</small></span></div><IconButton label="关闭" html-type="button" @click="rowDialog=undefined"><Icon icon="lucide:x" /></IconButton></header><section><div v-for="column in activeData.data?.columns" :key="column.name" class="form-field"><span><b>{{ column.name }}</b><small>{{ column.data_type }}{{ column.nullable?' · 可空':'' }}</small></span><WorkbenchSelect v-model="rowDialog.valueModes[column.name]" :aria-label="`记录字段 ${column.name} 写入方式`" :disabled="isGenerated(column)"><a-option value="default">使用默认值</a-option><a-option value="value">输入值</a-option><a-option v-if="column.nullable" value="null">NULL</a-option></WorkbenchSelect><CellValueInput v-if="rowDialog.valueModes[column.name]==='value'" v-model="rowDialog.values[column.name]" :column="column" :label="`记录字段 ${column.name}`" /></div></section><footer><a-button type="text" html-type="button" @click="rowDialog=undefined">取消</a-button><a-button type="primary" html-type="submit" class="primary">保存</a-button></footer></a-form></WorkbenchModal>
    <WorkbenchModal v-if="wizard" visible :width="880" :footer="false" hide-title :closable="false" :mask-closable="false" modal-class="workbench-dialog" @cancel="wizardRunning?sendWizardToBackground():(wizard=undefined)">
      <section class="modal operation-wizard" role="dialog" :aria-label="({import:'数据导入向导',export:'数据导出向导',generate:'测试数据生成器',dump:'SQL 转储向导',sql:'执行 SQL 文件'} as const)[wizard.kind]">
        <header><div><Icon icon="lucide:wand-sparkles" /><span><strong>{{ ({import:'数据导入向导',export:'数据导出向导',generate:'测试数据生成器',dump:'SQL 转储向导',sql:'执行 SQL 文件'} as const)[wizard.kind] }}</strong><small>{{ wizard.database }}{{ wizard.table?` · ${wizard.table}`:'' }}</small></span></div><IconButton label="关闭" @click="wizardRunning?sendWizardToBackground():(wizard=undefined)"><Icon icon="lucide:x" /></IconButton></header>
        <div class="wizard-steps"><span :class="{active:wizardStep===1}">1 · 来源与格式</span><span v-if="wizardHasMapping" :class="{active:wizardStep===2}">2 · 预览与字段映射</span><span :class="{active:wizardStep===wizardFinalStep}">{{ wizardFinalStep }} · 确认与执行</span></div>
        <div class="wizard-body">
          <p v-if="wizardError" class="inline-error" role="alert">{{ wizardError }}</p>
          <template v-if="wizardStep===1">
            <div v-if="wizard.kind==='import'||wizard.kind==='sql'" class="form-field file-drop"><FilePicker :accept="wizard.kind==='sql'?'.sql,.txt':'.csv,.json,.xlsx,.sql'" @change="wizardFile=($event.target as HTMLInputElement).files?.[0]" /><Icon icon="lucide:file-up" /><b>{{ wizardFile?.name || '选择文件' }}</b><span>支持 {{ wizard.kind==='sql'?'SQL/TXT':'CSV/JSON/XLSX/SQL' }}</span></div>
            <div v-if="wizard.kind==='export'" class="wizard-fields">
              <div class="form-field"><span>文件格式</span><WorkbenchSelect v-model="wizardFormat" aria-label="文件格式"><a-option value="csv">CSV</a-option><a-option value="xlsx">Excel XLSX</a-option><a-option value="json">JSON</a-option><a-option value="sql">SQL INSERT</a-option></WorkbenchSelect></div>
              <p>导出整个表的数据，不受当前分页限制。导出当前查询或所选区域，请使用表格上方的“导出结果”。</p>
              <WorkbenchCheckbox v-if="wizardFormat==='sql'" v-model="wizardIncludeStructure">包含重建表语句和完整数据</WorkbenchCheckbox>
              <p v-if="wizardIncludeStructure&&wizardFormat==='sql'">SQL 包含 DROP TABLE，导入时会覆盖目标同名表。</p>
              <fieldset v-if="!wizardIncludeStructure || wizardFormat!=='sql'" class="wizard-column-options"><legend>导出字段</legend><WorkbenchCheckbox v-for="column in wizardDefinitions" :key="column.name" :model-value="wizardColumns.includes(column.name)" :aria-label="`导出字段 ${column.name}`" @update:model-value="value=>setExportColumn(column.name,value)">{{ column.name }}</WorkbenchCheckbox></fieldset>
            </div>
            <div v-if="(wizard.kind==='import'&&!wizardIsSql&&(!wizardFile||/\.csv$/i.test(wizardFile.name))) || (wizard.kind==='export'&&wizardFormat==='csv')" class="wizard-fields">
              <div class="form-field"><span>CSV 分隔符</span><WorkbenchSelect v-model="wizardDelimiter" aria-label="CSV 分隔符"><a-option value=",">逗号 ,</a-option><a-option value=";">分号 ;</a-option><a-option :value="'\t'">制表符 Tab</a-option><a-option value="|">竖线 |</a-option></WorkbenchSelect></div>
              <div v-if="wizard.kind==='import'" class="form-field"><span>文件编码</span><WorkbenchSelect v-model="wizardEncoding" aria-label="CSV 文件编码"><a-option value="utf-8-sig">UTF-8（支持 BOM）</a-option><a-option value="gb18030">GB18030 / GBK</a-option></WorkbenchSelect></div>
              <div class="form-field"><span>NULL 标记</span><WorkbenchInput v-model="wizardNullMarker" aria-label="CSV NULL 标记" /></div><p>CSV 首行为字段名；引号、换行及中文会保留。空字段是空字符串，NULL 使用上面的标记；导出编码为 UTF-8 BOM。</p>
            </div>
            <div v-if="wizard.kind==='dump'" class="wizard-fields"><WorkbenchCheckbox v-model="wizardIncludeData">包含表数据</WorkbenchCheckbox></div>
            <div v-if="wizard.kind==='generate'" class="wizard-fields"><div class="form-field"><span>生成行数</span><WorkbenchInput v-model.number="wizardCount" aria-label="生成行数" type="number" min="1" max="10000" /></div></div>
          </template>
          <template v-else-if="wizardHasMapping&&wizardStep===2&&wizardPreview">
            <p>已读取 {{ wizardPreview.row_count }} 行。将来源字段映射到目标字段，可跳过不需要的列。</p>
            <div class="wizard-mapping"><div v-for="name in wizardPreview.source_columns" :key="name" class="form-field"><span>{{ name }}</span><WorkbenchSelect v-model="wizardMapping[name]" :aria-label="`字段映射 ${name}`"><a-option value="">跳过此字段</a-option><a-option v-for="column in wizardPreview.target_columns" :key="column.name" :value="column.name">{{ column.name }} · {{ column.data_type }}</a-option></WorkbenchSelect></div></div>
            <div class="wizard-preview-table"><table><thead><tr><th v-for="name in wizardPreview.source_columns" :key="name">{{ name }}</th></tr></thead><tbody><tr v-for="(row,index) in wizardPreview.rows" :key="index"><td v-for="name in wizardPreview.source_columns" :key="name">{{ row[name]===null?'NULL':row[name] }}</td></tr></tbody></table></div>
          </template>
          <div v-else class="wizard-summary"><strong>操作确认</strong><dl><div><dt>目标</dt><dd>{{ wizard.database }}{{ wizard.table&&!wizardIsSql?`.${wizard.table}`:'' }}</dd></div><div v-if="wizardFile"><dt>文件</dt><dd>{{ wizardFile.name }} · {{ (wizardFile.size/1024/1024).toFixed(wizardFile.size>10*1024*1024?1:2) }} MB</dd></div><div v-if="wizardPreview"><dt>记录</dt><dd>{{ wizardPreview.row_count }} 行，映射 {{ Object.values(wizardMapping).filter(Boolean).length }} 个字段</dd></div><div v-if="wizard.kind==='export'"><dt>导出</dt><dd>{{ wizardFormat==='xlsx'?'Excel XLSX':wizardFormat.toUpperCase() }} · {{ wizardIncludeStructure&&wizardFormat==='sql'?'结构与完整数据':`${wizardColumns.length} 个字段` }}</dd></div><div v-if="wizard.kind==='generate'"><dt>记录</dt><dd>{{ wizardCount }} 行</dd></div></dl><p v-if="wizardIsSql" class="wizard-note"><Icon icon="lucide:info" />SQL 将按文件内容在目标数据库中创建或写入对象。</p><p v-if="wizardHasMapping">导入在事务中执行；事务表任一记录失败时整体回滚。</p><a-progress v-if="wizardRunning" :percent="jobProgress.value/100" :show-text="false" /><p v-if="wizardRunning">{{ jobProgress.message || '正在执行…' }} · {{ jobProgress.value }}%</p></div>
        </div>
        <footer><a-button type="text" :disabled="jobCancelling||wizardPreviewing" @click="wizardRunning?cancelJob():(wizard=undefined)">{{ jobCancelling?'正在取消…':wizardRunning?'取消任务':'取消' }}</a-button><a-button v-if="wizardRunning" type="text" @click="sendWizardToBackground">转到后台</a-button><a-button v-if="wizardStep>1" type="text" :disabled="wizardRunning||wizardPreviewing" @click="wizardStep--">上一步</a-button><a-button v-if="wizardStep<wizardFinalStep" type="primary" class="primary" :loading="wizardPreviewing" :disabled="((wizard.kind==='import'||wizard.kind==='sql')&&!wizardFile)||(wizard.kind==='export'&&!wizardDefinitions.length)" @click="wizardNext">下一步</a-button><a-button v-else type="primary" class="primary" :disabled="wizardRunning" @click="finishWizard">{{ wizardRunning?'后台执行中…':'开始执行' }}</a-button></footer>
      </section>
    </WorkbenchModal>
    <WorkbenchModal v-if="searchDialog" visible :width="760" :footer="false" hide-title :closable="false" :mask-closable="false" modal-class="workbench-dialog" @cancel="searchDialog=undefined"><section class="modal search-modal"><header><div><Icon icon="lucide:search" /><span><strong>在数据库中查找</strong><small>“{{ searchDialog.term }}”</small></span></div><IconButton label="关闭" @click="searchDialog=undefined"><Icon icon="lucide:x" /></IconButton></header><div class="search-results"><a-button v-for="(row,index) in searchDialog.rows" :key="index" type="text" @dblclick="openSearchResult(row)"><Icon :icon="row.kind==='table'?'lucide:table-2':'lucide:columns-3'" /><span><b>{{ row.name }}</b><small>{{ row.kind }} · {{ row.detail }}</small></span></a-button></div><footer><span>{{ searchDialog.rows.length }} 个结果</span><a-button type="text" @click="searchDialog=undefined">关闭</a-button></footer></section></WorkbenchModal>
  </main>
</template>

<style scoped>
.wizard-mapping { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 18px; margin:12px 0; }
.wizard-column-options { display:flex; flex-wrap:wrap; gap:8px 16px; max-height:160px; overflow:auto; padding:12px; border:1px solid #dce3ec; }
.wizard-preview-table { max-height:220px; overflow:auto; border:1px solid #dce3ec; }
.wizard-preview-table table { border-collapse:collapse; font-size:12px; min-width:100%; }
.wizard-preview-table th,.wizard-preview-table td { min-width:100px; max-width:250px; padding:6px 10px; border:1px solid #e3e8ef; white-space:pre-wrap; overflow-wrap:anywhere; }
.wizard-preview-table th { position:sticky; top:0; background:#f3f6fa; }
</style>
