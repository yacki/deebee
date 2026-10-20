<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { Icon } from "@iconify/vue";
import BrandMark from "./BrandMark.vue";
import { API_BASE, authToken } from "../api";
import JsonPanel from "./JsonPanel.vue";
import SessionRecordings from "./SessionRecordings.vue";
import ApprovalCenter from "./ApprovalCenter.vue";
import ResourceCatalog from "./ResourceCatalog.vue";
import ResourceIcon from "./ResourceIcon.vue";
import { environmentLabel, resourceProduct } from "../resourcePresentation";
import AccessActions from "./AccessActions.vue";
import RoleManager from "./RoleManager.vue";
import RoleMemberships from "./RoleMemberships.vue";
import SshTerminal from "./SshTerminal.vue";
import RdpDesktop from "./RdpDesktop.vue";
import { accessNavigation, actionOptions, optionLabel, selectedGrantAccounts, grantAccountFields, localDateTime, expiryTimestamp } from "../accessPolicy";

// Management records are server-validated tagged JSON objects; forms use field definitions below.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
type Field = { key: string; label: string; type?: "password" | "number" | "check" | "select" | "list" | "textarea" | "json" | "tags" | "environment"; options?: string[]; collection?: string; hint?: string; required?: boolean };
type Section = { id: string; name: string; icon: string; description: string; fields?: Field[] };

const sections: Section[] = [
  { id:"roles", name:"用户组管理", icon:"lucide:users-round", description:"把人员或 Agent 分配到用户组，按资源绑定真实账号和允许操作。用户组不授予后台管理权限。", fields:[
    {key:"name",label:"用户组名称",required:true},{key:"description",label:"用户组说明",type:"textarea"},{key:"enabled",label:"启用用户组",type:"check"}]},
  { id:"role-memberships", name:"用户组成员", icon:"lucide:users", description:"管理系统账号所属用户组。", fields:[
    {key:"role_id",label:"用户组",type:"select",collection:"roles",required:true},{key:"principal_id",label:"系统账号",type:"select",collection:"principals",required:true},{key:"enabled",label:"启用成员关系",type:"check"}]},
  { id:"principals", name:"系统账号", icon:"lucide:users", description:"系统内的账号，通过授权使用各个资源账号。", fields:[
    {key:"name",label:"显示名称",required:true},{key:"kind",label:"账号类型",type:"select",options:["human","service"]},
    {key:"username",label:"登录用户名",hint:"仅人员身份可选；留空表示仅通过外部身份或 API-Key 使用。"},
    {key:"password",label:"登录密码",type:"password",hint:"设置或重置至少 12 位；编辑时留空表示不修改。"},{key:"enabled",label:"启用系统账号",type:"check"}]},
  { id:"identity-sources", name:"身份验证服务", icon:"lucide:shield-check", description:"API-Key 可本地签发或由可信服务验证；OIDC 接收面向 DeeBee 的 Access Token，不接受 ID Token 调 API。", fields:[
    {key:"name",label:"身份验证服务名称",required:true},{key:"type",label:"身份验证服务类型",type:"select",options:["api_key","oidc"]},
    {key:"validation_mode",label:"验证方式",type:"select",options:["managed","external_http","jwt","introspection"]},
    {key:"issuer",label:"OIDC Issuer",hint:"精确匹配，不带发现文档路径。"},{key:"audiences",label:"允许的 Audience",type:"list",hint:"逗号分隔；必须为 DeeBee 签发。"},
    {key:"allowed_algorithms",label:"允许的签名算法",type:"list",hint:"RS256 或 ES256，逗号分隔。"},
    {key:"field_mapping",label:"外部验证字段映射（JSON）",type:"json",hint:"可选，例如 {\"subject\":\"user.id\"}。"},
    {key:"verify_endpoint",label:"外部 API-Key 验证地址"},{key:"introspection_endpoint",label:"Token Introspection 地址"},
    {key:"service_client_id",label:"验证服务 Client ID"},{key:"service_secret",label:"验证服务凭据",type:"password"},
    {key:"required_scopes",label:"验证服务要求的权限名称",type:"list"},{key:"allowed_client_ids",label:"允许的客户端 ID",type:"list"},
    {key:"trusted_origins",label:"额外可信服务 Origin",type:"list",hint:"仅填经批准的 HTTPS Origin；不要添加不相关域名。"},
    {key:"browser_login",label:"启用 OIDC 浏览器登录",type:"check"},{key:"client_id",label:"浏览器登录 Client ID"},{key:"client_secret",label:"浏览器登录 Client Secret",type:"password"},
    {key:"enabled",label:"启用身份验证服务",type:"check"}]},
  { id:"identity-bindings", name:"外部身份", icon:"lucide:link", description:"外部调用方的身份，绑定到一个系统账号。", fields:[
    {key:"source_id",label:"身份验证服务",type:"select",collection:"identity-sources",required:true},
    {key:"subject",label:"外部身份标识",required:true,hint:"OIDC 的 sub，或外部 API-Key 验证服务返回的 subject。"},
    {key:"principal_id",label:"系统账号",type:"select",collection:"principals",required:true},{key:"enabled",label:"启用外部身份",type:"check"}]},
  { id:"resources", name:"资源管理", icon:"lucide:server", description:"管理数据库、服务器及其资源账号。前台保存的连接自动出现在这里。", fields:[
    {key:"name",label:"资源名称",required:true},{key:"environment",label:"环境",type:"environment",hint:"选择已有环境或填写自定义名称，如生产、测试、开发。"},{key:"project_groups",label:"项目组",type:"tags"},{key:"tags",label:"标签",type:"tags",hint:"例如 AMD64、ARM64、核心业务。"},{key:"type",label:"资源类型",type:"select",options:["ssh","mysql","postgresql","k8s","rdp"]},
    {key:"host",label:"主机地址",required:true},{key:"port",label:"端口",type:"number",required:true},
    {key:"domain",label:"Windows 域（可选）"},{key:"rdp_security",label:"远程桌面安全模式",type:"select",options:["nla","tls","rdp"]},
    {key:"database",label:"数据库名称",hint:"MySQL/PostgreSQL 必填，一个资源限定一个业务数据库。"},
    {key:"namespaces",label:"获准 Namespace",type:"list",hint:"只允许在这些 Namespace 内进行查询或维护，逗号分隔。"},{key:"schemas",label:"PostgreSQL Schema",type:"list"},{key:"host_key",label:"SSH 主机 SHA256 指纹",hint:"从可信渠道核验，必须在用户认证前验证。"},
    {key:"host_key_algorithm",label:"SSH 主机密钥算法",type:"select",options:["ssh-ed25519","ecdsa-sha2-nistp256","rsa-sha2-512","rsa-sha2-256"]},
    {key:"tls",label:"验证 TLS",type:"check"},{key:"ca_file",label:"服务器上的 CA 文件路径"},
    {key:"timeout_seconds",label:"执行超时上限（秒）",type:"number"},{key:"max_rows",label:"返回行数上限",type:"number"},
    {key:"enabled",label:"启用资源",type:"check"}]},
  { id:"accounts", name:"资源账号", icon:"lucide:key-round", description:"登录数据库或服务器所使用的账号。连接成功后，仍需核实权限再启用。", fields:[
    {key:"resource_id",label:"所属资源",type:"select",collection:"resources",required:true},{key:"name",label:"账号别名",required:true},
    {key:"username",label:"登录用户名",required:true},{key:"tier",label:"账号级别",type:"select",options:["normal","privileged"]},
    {key:"auth_method",label:"登录方式",type:"select",options:["password","private_key"]},{key:"password",label:"密码 / Kubernetes Bearer Token",type:"password"},
    {key:"private_key",label:"SSH 私钥",type:"textarea",hint:"只用于 SSH 私钥认证；保存后不回显。"},{key:"passphrase",label:"私钥口令",type:"password"},
    {key:"permission_confirmed",label:"已核验资源账号的真实权限",type:"check"},{key:"permission_note",label:"权限核验说明",type:"textarea",hint:"普通数据库账号必须真正只读；SSH 普通账号仍可修改它有权修改的文件。"},
    {key:"enabled",label:"启用资源账号（需先检查连接）",type:"check"}]},
  { id:"access-grants", name:"资源授权", icon:"lucide:badge-check", description:"决定每个系统账号可以使用哪些资源账号。", fields:[
    {key:"principal_id",label:"系统账号",type:"select",collection:"principals",required:true},{key:"role_id",label:"用户组",type:"select",collection:"roles",required:true},{key:"resource_id",label:"目标资源",type:"select",collection:"resources",required:true},
    {key:"normal_account_id",label:"普通账号",type:"select",collection:"accounts",required:true},{key:"privileged_account_id",label:"特权账号（可选）",type:"select",collection:"accounts"},
    {key:"allow_privileged",label:"允许使用特权账号",type:"check",hint:"仅在调用显式请求 privileged 且凭据具备 privilege:use 时使用。"},
    {key:"actions",label:"允许操作",type:"list"},{key:"limits",label:"执行限制",type:"json"},
    {key:"expires_at",label:"授权到期 Unix 秒（可选）",type:"number"},{key:"enabled",label:"启用资源授权",type:"check"}]},
  { id:"api-keys", name:"访问凭证（API Key）", icon:"lucide:fingerprint", description:"用于识别外部身份的 API Key，绑定到一个系统账号。密钥权限不能超过资源授权。", fields:[
    {key:"name",label:"凭证名称",required:true},{key:"principal_id",label:"系统账号",type:"select",collection:"principals",required:true},
    {key:"source_id",label:"身份验证服务",type:"select",collection:"identity-sources",required:true},{key:"expires_in_days",label:"有效天数",type:"number"},
    {key:"scopes",label:"允许操作",type:"list",hint:"resources:read；只读检查使用 ssh:inspect、db:query、k8s:read。修改另需对应权限及 privilege:use。"},
    {key:"resource_ids",label:"限定可用资源（可选）",type:"list",hint:"留空仍受系统账号的显式资源授权约束。"}]},
  { id:"preview", name:"查看可用权限", icon:"lucide:scan-eye", description:"检查系统账号最终可使用的资源账号。凭证权限可能进一步限制访问。"},
  { id:"executions", name:"执行记录", icon:"lucide:activity", description:"执行实际使用的账号、状态与结果。unknown 需要核实远端状态，不应自动重放修改。"},
  { id:"approvals", name:"操作审批", icon:"lucide:clipboard-check", description:"审核数据库和 SSH 的危险操作，配置审批规则和危险命令模板。"},
  { id:"session-recordings", name:"会话录像", icon:"lucide:clapperboard", description:"SSH 与 RDP 会话自动录制；结束会话后可回放或下载。仅管理员可查看。" },
  { id:"audit-events", name:"审计日志", icon:"lucide:scroll-text", description:"按真实登录身份归档 MCP、授权用户、管理端和原工作台操作；请求保留 1 KiB，返回内容保留 4 KiB，均先脱敏。历史截断内容无法恢复。"},
  { id:"integration", name:"系统设置", icon:"lucide:plug", description:"配置外部身份的验证方式、程序接入地址和配置备份。"},
];
const recordingsPanel = ref<InstanceType<typeof SessionRecordings>>();
const approvalsPanel = ref<InstanceType<typeof ApprovalCenter>>();
const approvalReason = ref(''), userHistory = ref<Row[]>([]);
async function loadUserHistory(){userHistory.value=(await request('/v1/executions','GET',undefined,false)).items;}
const location = window.location;
const isUser = ref(location.hash === "#access-user");
const loggedIn = ref(false); const busy = ref(false); const message = ref(""); const error = ref("");
const loginName = ref("admin"); const loginPassword = ref(""); const current = ref("resources");
const records = ref<Record<string, Row[]>>({}); const form = ref<Row | null>(null); const editing = ref<Row | null>(null);
const report = ref<Row | null>(null); const reportType = ref(""); const reportTitle = ref(""); const newKey = ref(""); const sourceCredential = ref(""); const testingSource = ref<Row | null>(null);
const csrf = ref(""); const me = ref<Row>({}); const status = ref<Row>({}); const loginSources = ref<Row[]>([]);
const previewPrincipal = ref(""); const previewScopes = ref("resources:read, ssh:exec, db:query, db:write, privilege:use");
const sshCheck = ref("overview"); const kubeNamespace = ref(""); const kubeKind = ref("pods"); const kubeName = ref(""); const kubeOperation = ref("list");
const userResources = ref<Row[]>([]); const selectedResource = ref(""); const mode = ref("normal"); const operation = ref("db.query"); const command = ref("SELECT 1"); const userExecution = ref<Row | null>(null);
const configText = ref(""); const importPreview = ref<Row | null>(null);
watch(configText,()=>{importPreview.value=null;});
const userSchema = ref<Row | null>(null);
const legacyItems = ref<Row[] | null>(null);
const auditActor = ref(""); const auditOperation = ref(""); const auditSurface = ref("");
async function previewLegacy(){await act(async()=>{legacyItems.value=(await request('/admin/v1/legacy-connections/preview')).items;});}
async function importLegacy(item:Row){await act(async()=>{reportType.value="generic";reportTitle.value="连接导入结果";report.value=await request('/admin/v1/legacy-connections/import','POST',{profile_id:item.id,fingerprint:item.fingerprint});await load();message.value='已建立禁用引用，原连接未改动。请核验传输安全和账号权限后再发布。';});}
watch([selectedResource,mode],()=>{userSchema.value=null;userExecution.value=null;operation.value=defaultOperation();});
const section = computed(()=>sections.find(s=>s.id===current.value)!);
const resourceContext = ref("");
const mainElement = ref<HTMLElement | null>(null);
watch(current, async () => { await nextTick(); mainElement.value?.scrollTo({top:0}); });
watch(form, async value => { if(value){await nextTick();mainElement.value?.scrollTo({top:0});} });
const deleting = ref<Row | null>(null), deleteName = ref("");
function askDelete(item:Row){deleting.value=item;deleteName.value="";}
async function removeResource(){await act(async()=>{if(!deleting.value)return;await request('/admin/v1/resources/'+deleting.value.id,'DELETE',{expected_version:deleting.value.version,confirm_name:deleteName.value});deleting.value=null;await load();message.value="资源已删除，关联账号与授权已撤销。工作台连接及远端数据未改动。";});}
function classificationOptions(key:string):string[]{return [...new Set([...(key==='tags'?['AMD64','ARM64']:[]),...(records.value.resources||[]).flatMap(r=>r[key]||[])])];}
function checkArray(values:string[],value:string,checked:unknown){const i=values.indexOf(value);if(checked&&i<0)values.push(value);else if(!checked&&i>=0)values.splice(i,1);}

const principalContext = ref(""); const principalSearch = ref("");
const resourceDetail = computed(() => records.value.resources?.find(r => r.id === resourceContext.value));
const principalDetail = computed(() => records.value.principals?.find(r => r.id === principalContext.value));
const navigationId = computed(() => current.value === "accounts" ? "resources" : current.value === "api-keys" ? "identity-bindings" : current.value === "identity-sources" ? "integration" : current.value === "preview" ? "access-grants" : current.value);
const rows = computed(() => (records.value[current.value] || []).filter(item =>
  (current.value !== "accounts" || item.resource_id === resourceContext.value) &&
  (current.value !== "principals" || String(item.name+" "+(item.username||"")).toLowerCase().includes(principalSearch.value.toLowerCase())) &&
  (!principalContext.value || !["identity-bindings","api-keys","access-grants"].includes(current.value) || item.principal_id === principalContext.value)));
const grantIds = ref<string[]>([]);
const grantActions = ref<string[]>([]);
const credentialActions = ref<string[]>([]);
const credentialResources = ref<string[]>([]);
const grantExpiry = ref(""); const grantStart = ref(""); const grantSubjectType = ref("role");
const batchResources = ref<string[]>([]), batchAccounts = ref<Record<string,string[]>>({}), batchActions = ref<Record<string,string[]>>({});
const accessOption = ref(""); const sessionOpen = ref(false);
const activeAccess = computed(()=>activeResource.value?.access_modes.find((m:Row)=>m.access_option_id===accessOption.value));
const sessionAccess = computed(()=>({resourceId:selectedResource.value,mode:mode.value,optionId:accessOption.value,csrf:csrf.value}));
watch(accessOption,()=>{mode.value=activeAccess.value?.mode||"normal";operation.value=defaultOperation();sessionOpen.value=false;userSchema.value=null;userExecution.value=null;});
watch(batchResources,ids=>{for(const id of ids){batchAccounts.value[id]??=[];batchActions.value[id]??=["resources:read"];}});
function subjectName(g:Row){return g.role_id?label('roles',g.role_id)+'（用户组）':label('principals',g.principal_id)+'（直接授权）';}
function effective(item:Row){const now=Date.now()/1000;return item.enabled&&!item.deleted_at&&(!item.starts_at||item.starts_at<=now)&&(!item.expires_at||item.expires_at>now);}
function inheritedGrants(id:string):Row[]{const roleIds=(records.value['role-memberships']||[]).filter(m=>m.principal_id===id&&effective(m)&&records.value.roles?.some(r=>r.id===m.role_id&&effective(r))).map(m=>m.role_id);return (records.value['access-grants']||[]).filter(g=>roleIds.includes(g.role_id)&&effective(g));}
function grantedPeople(g:Row):string{if(!g.role_id)return label('principals',g.principal_id);if(!records.value.roles?.some(r=>r.id===g.role_id&&effective(r)))return '用户组已停用';return (records.value['role-memberships']||[]).filter(m=>m.role_id===g.role_id&&effective(m)&&records.value.principals?.some(p=>p.id===m.principal_id&&p.enabled)).map(m=>label('principals',m.principal_id)).join('、')||'暂无生效成员';}
async function addRoleGrant(role:Row){await navigate('access-grants');start();grantSubjectType.value='role';form.value!.role_id=role.id;}
async function editRoleGrant(grant:Row){await navigate('access-grants');start(grant);}

const grantLimits = ref({ timeout_seconds: 60, max_rows: 1000, max_output_bytes: 1048576 });
const grantChoices = computed(() => (records.value.accounts || []).filter(a => a.resource_id === form.value?.resource_id));
const grantResource = computed(() => records.value.resources?.find(r => r.id === form.value?.resource_id));
function accountGrants(id: string): Row[] { return (records.value["access-grants"] || []).filter(g => g.normal_account_id === id || (g.allow_privileged && g.privileged_account_id === id)); }
function principalRows(collection: string): Row[] { return (records.value[collection] || []).filter(r => r.principal_id === principalContext.value); }
function grantNames(item: Row) { return selectedGrantAccounts(item).map(id => {
  const a = records.value.accounts?.find(a => a.id === id); return a ? a.username + "（" + optionLabel(a.tier) + "）" : id;
}).join("、") || "未选择资源账号"; }
async function openAccounts(resource: Row) { resourceContext.value = resource.id; await select("accounts"); }
async function openPrincipal(principal: Row) { principalContext.value = principal.id; await select("principals"); }
async function principalSection(id: string) { await select(id); }
function accountPermissionLabel(item: Row) { return item.permission_confirmed ? optionLabel(item.tier) : "权限待核实"; }
function connectionNotice(item?: Row) { return String(item?.connection_sync?.reason || "").replaceAll("已入池；", "").replaceAll("已加入资源管理；", "").replaceAll("前台连接", "工作台连接").replaceAll("启用 MCP 资源", "启用资源"); }
function externalName(item: Row) { const keys=(records.value["api-keys"]||[]).filter(k=>k.source_id===item.source_id&&k.subject===item.subject); return keys.length ? keys.map(k=>k.name).join("、") : item.subject; }
function accountHealth(item: Row) { return !item.test_result?.connected ? "连接待检查" : !item.permission_confirmed ? "权限待核实" : "已核实权限"; }
function changedGrantResource() { grantIds.value = []; grantActions.value = ["resources:read"]; }

const activeResource = computed(()=>userResources.value.find(r=>r.id===selectedResource.value));
const visibleFields = computed(()=>(section.value.fields||[]).filter(field=>{
  const f=form.value;if(!f)return true;
  if(current.value==="accounts"&&field.key==="resource_id")return false;
  if(current.value==="access-grants")return [grantSubjectType.value==='role'?"role_id":"principal_id","resource_id","enabled"].includes(field.key);
  if(current.value==="api-keys"&&["scopes","resource_ids"].includes(field.key))return false;
  if(editing.value?.connection_sync){
    const sourceFields=current.value==='resources'?["name","type","host","port","database","schemas","host_key"]:current.value==='accounts'?["resource_id","username","auth_method","password","private_key","passphrase"]:[];
    if(sourceFields.includes(field.key))return false;
  }
  if(current.value==="principals"&&f.kind==="service")return !["username","password"].includes(field.key);
  if(current.value==="identity-sources"){
    const always=["name","type","validation_mode","enabled"];
    if(always.includes(field.key))return true;
    if(f.validation_mode==="managed")return false;
    if(f.type==="api_key")return ["audiences","verify_endpoint","service_secret","required_scopes","allowed_client_ids","trusted_origins","field_mapping"].includes(field.key);
    if(["verify_endpoint","field_mapping"].includes(field.key))return false;
    if(["introspection_endpoint","service_client_id","service_secret"].includes(field.key))return f.validation_mode==="introspection";
    if(["client_id","client_secret"].includes(field.key))return f.browser_login;
  }
  if(current.value==="resources"){
    if(["domain","rdp_security"].includes(field.key))return f.type==="rdp";
    if(f.type==="rdp")return !["database","schemas","host_key","host_key_algorithm","namespaces","tls","ca_file","max_rows"].includes(field.key);
    if(field.key==="namespaces")return f.type==="k8s";
    if(f.type==="k8s"&&field.key==="database")return false;
    if(f.type==="ssh")return !["database","schemas","tls","ca_file","max_rows"].includes(field.key);
    if(["host_key","host_key_algorithm"].includes(field.key))return false;
    if(field.key==="schemas")return f.type==="postgresql";
  }
  if(current.value==="accounts"){
    if(["private_key","passphrase"].includes(field.key))return f.auth_method==="private_key";
    if(field.key==="password")return f.auth_method==="password";
  }
  return true;
}));
function fieldOptions(field:Field){if(field.key==="validation_mode")return form.value?.type==="oidc"?["jwt","introspection"]:["managed","external_http"];if(current.value==="accounts"&&field.key==="auth_method"&&resourceDetail.value?.type!=="ssh")return ["password"];return field.options;}
function lockedField(field:Field){return Boolean(editing.value&&current.value==="identity-sources"&&["type","issuer","validation_mode"].includes(field.key));}
watch(()=>form.value?.type,(value,previous)=>{if(!form.value||!previous||value===previous)return;if(current.value==="identity-sources")form.value.validation_mode=value==="oidc"?"jwt":"managed";if(current.value==="resources")form.value.port=value==="ssh"?22:value==="mysql"?3306:value==="k8s"?6443:value==="rdp"?3389:5432;});

async function request(path: string, method="GET", body?: unknown, admin=true): Promise<Row> {
  const headers: Record<string,string> = {};
  if(admin && authToken()) headers.Authorization=`Bearer ${authToken()}`;
  if(body!==undefined) headers["Content-Type"]="application/json";
  if(!admin && csrf.value) headers["X-CSRF-Token"]=csrf.value;
  const response=await fetch(`${API_BASE}${path}`,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:"same-origin"});
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(data.error?.message || (Array.isArray(data.detail)?data.detail.map((d:Row)=>d.msg).join("；"):data.detail) || `请求失败 ${response.status}`);
  return data;
}
async function act(action: ()=>Promise<void>) { busy.value=true; error.value=""; message.value=""; try{await action();}catch(e){error.value=e instanceof Error?e.message:"操作失败";}finally{busy.value=false;} }
async function load() {
  if(isUser.value){ const result=await request("/v1/me","GET",undefined,false); me.value=result;csrf.value=result.csrf || "";const resources:Row[]=[];let cursor="";do{const page=await request("/v1/resources"+(cursor?"?cursor="+encodeURIComponent(cursor):""),"GET",undefined,false);resources.push(...page.resources);cursor=page.next_cursor||"";}while(cursor);userResources.value=resources;if(selectedResource.value&&!resources.some(r=>r.id===selectedResource.value)){sessionOpen.value=false;selectedResource.value="";accessOption.value="";}else if(accessOption.value&&!activeResource.value?.access_modes.some((m:Row)=>m.access_option_id===accessOption.value)){sessionOpen.value=false;accessOption.value="";}loggedIn.value=true;return; }
  status.value=await request("/admin/v1/status");
  for(const s of sections.filter(s=>s.fields)) records.value[s.id]=(await request(`/admin/v1/${s.id}`)).items;
  loggedIn.value=true;
  await refresh();
}
async function login(){await act(async()=>{if(isUser.value){const result=await request("/v1/local/login","POST",{username:loginName.value,password:loginPassword.value},false);csrf.value=result.csrf;}else{const result=await request("/auth/login","POST",{username:loginName.value,password:loginPassword.value},false);sessionStorage.setItem("deebee_token",result.token);}loginPassword.value="";await load();});}
async function refresh(){
  if(current.value==="approvals"){await approvalsPanel.value?.refresh();return;}
  if(current.value==="session-recordings"){await recordingsPanel.value?.refresh();return;}
  if(current.value==="audit-events"){
    const query=new URLSearchParams();if(auditActor.value)query.set("actor",auditActor.value);if(auditOperation.value)query.set("operation",auditOperation.value);if(auditSurface.value)query.set("surface",auditSurface.value);
    records.value[current.value]=(await request(`/admin/v1/audit-events?${query}`)).items;
  }else if(current.value==="executions")records.value[current.value]=(await request("/admin/v1/executions")).items;
  else if(section.value.fields)records.value[current.value]=(await request(`/admin/v1/${current.value}`)).items;
}
async function select(id:string){
  if(id!=="accounts")resourceContext.value="";
  current.value=id;form.value=null;report.value=null;newKey.value="";testingSource.value=null;sourceCredential.value="";
  await act(async()=>{ await load(); });
}
async function navigate(id:string){principalContext.value="";await select(id);}

function options(field:Field):Row[]{let items=records.value[field.collection || ""] || [];if(field.key==="source_id"&&current.value==="api-keys")items=items.filter(i=>i.validation_mode==="managed"&&i.enabled);if(field.key.endsWith("account_id"))items=items.filter(i=>i.resource_id===form.value?.resource_id&&i.tier===(field.key==="normal_account_id"?"normal":"privileged"));return items;}
function start(item?:Row){
  if(current.value==="accounts"&&!resourceContext.value){error.value="请先选择资源。";return;}
  editing.value=item||null;error.value="";report.value=null;newKey.value="";
  const defaults:Record<string,Row>={roles:{enabled:true},principals:{kind:"human",enabled:true},"identity-sources":{type:"api_key",validation_mode:"managed",enabled:false},"identity-bindings":{enabled:true},resources:{type:"ssh",port:22,namespaces:["default"],schemas:["public"],tls:true,rdp_security:"nla",enabled:false,timeout_seconds:60,max_rows:1000},accounts:{tier:"normal",auth_method:"password",enabled:false,permission_confirmed:false},"access-grants":{enabled:true,allow_privileged:false,actions:["resources:read"],limits:{timeout_seconds:60,max_rows:1000,max_output_bytes:1048576}},"api-keys":{source_id:"local_keys",expires_in_days:90,scopes:["resources:read","ssh:exec","db:query"]}};
  form.value={...defaults[current.value]};
  for(const f of section.value.fields||[]){let v=item?.[f.key]??form.value[f.key]??(f.key==="allowed_algorithms"?["RS256"]:f.key==="host_key_algorithm"?"ssh-ed25519":f.type==="check"?false:"");if(f.type==="tags")v=Array.isArray(v)?v:[];if(f.type==="password"||f.key==="private_key")v="";if(f.type==="list")v=Array.isArray(v)?v.join(", "):v;if(f.type==="json")v=JSON.stringify(v||{},null,2);form.value[f.key]=v;}
  if(current.value==="accounts")form.value.resource_id=resourceContext.value;
  if(principalContext.value&&["identity-bindings","api-keys","access-grants"].includes(current.value))form.value.principal_id=principalContext.value;
  if(current.value==="access-grants"){
    grantSubjectType.value=item?(item.role_id?"role":"principal"):principalContext.value?"principal":"role";
    batchResources.value=[];batchAccounts.value={};batchActions.value={};
    grantStart.value=localDateTime(item?.starts_at);
    grantIds.value=selectedGrantAccounts(item||{});
    grantActions.value=[...(item?.actions||["resources:read"])];
    grantExpiry.value=localDateTime(item?.expires_at);
    grantLimits.value={timeout_seconds:60,max_rows:1000,max_output_bytes:1048576,...item?.limits};
  }
  if(current.value==="api-keys"){credentialActions.value=["resources:read"];credentialResources.value=[];}
}
async function save(){await act(async()=>{if(!form.value)return;const body:Row={};for(const f of visibleFields.value){if(f.required&&!String(form.value[f.key]??"").trim())throw new Error("请填写"+f.label);}
// A newly selected identity protocol must not submit fields left hidden by the previous protocol.
for(const f of current.value==="identity-sources"?visibleFields.value:section.value.fields||[]){const value=form.value[f.key];body[f.key]=f.type==="json"?JSON.parse(String(value)||"{}"):f.type==="list"?String(value).split(",").map(v=>v.trim()).filter(Boolean):f.type==="number"?(value===""?null:Number(value)):f.type==="environment"?(value||""):value;}
  if(current.value==="access-grants"){
    body.principal_id=grantSubjectType.value==="principal"?body.principal_id:"";body.role_id=grantSubjectType.value==="role"?body.role_id:"";
    body.starts_at=editing.value&&grantStart.value===localDateTime(editing.value.starts_at)?editing.value.starts_at:expiryTimestamp(grantStart.value);
    Object.assign(body,grantAccountFields(grantIds.value,(records.value.accounts||[]).map(a=>({id:a.id,resource_id:a.resource_id,tier:a.tier,enabled:a.enabled})),String(body.resource_id),Boolean(body.enabled)));
    body.actions=[...grantActions.value];body.expires_at=editing.value&&grantExpiry.value===localDateTime(editing.value.expires_at)?editing.value.expires_at:expiryTimestamp(grantExpiry.value);body.limits={...grantLimits.value};
    if(body.allow_privileged&&!body.actions.includes("privilege:use"))throw new Error("已选择高权限资源账号，请明确勾选允许使用高权限资源账号，或取消选择该账号。");
    // Preserve a dormant legacy link without silently activating it.
    if(editing.value&&!body.allow_privileged&&editing.value.resource_id===body.resource_id)body.privileged_account_id=editing.value.privileged_account_id||"";
  }
  if(current.value==="api-keys"){body.scopes=[...credentialActions.value];body.resource_ids=[...credentialResources.value];}
  if(editing.value?.connection_sync){for(const key of (current.value==='resources'?["name","type","host","port","database","schemas","host_key"]:["resource_id","username","auth_method","password","private_key","passphrase"]))delete body[key];}
  if(current.value==="principals"&&body.kind==="service"){body.username="";body.password="";}
  if(current.value==="api-keys"){const result=await request("/admin/v1/api-keys","POST",body);newKey.value=result.key;}
  else if(current.value==="access-grants"&&!editing.value&&batchResources.value.length){
    const grants=[body,...batchResources.value.filter(id=>id!==body.resource_id).map(id=>({...body,resource_id:id,...grantAccountFields(batchAccounts.value[id]||[],(records.value.accounts||[]).map(a=>({id:a.id,resource_id:a.resource_id,tier:a.tier,enabled:a.enabled})),id,Boolean(body.enabled)),actions:batchActions.value[id]||["resources:read"]}))];
    if(grants.some(g=>g.allow_privileged&&!g.actions.includes("privilege:use")))throw new Error("每个高权限账号均须明确勾选使用高权限资源账号。");
    await request('/admin/v1/access-grants/batch','POST',{grants});
  }
  else{if(editing.value)body.expected_version=editing.value.version;await request(`/admin/v1/${current.value}${editing.value?`/${editing.value.id}`:""}`,editing.value?"PATCH":"POST",body);}
  form.value=null;await load();message.value="已保存。授权变化会立即拒绝后续不再获准的操作。";
});}
async function toggle(item:Row){await act(async()=>{if(current.value==="api-keys"){await request(`/admin/v1/api-keys/${item.id}/revoke`,"POST");}else{await request(`/admin/v1/${current.value}/${item.id}`,"PATCH",{expected_version:item.version,enabled:!item.enabled});}await load();message.value=item.enabled?"已停用/撤销":"已启用";});}
async function testAccount(item:Row){await act(async()=>{const result=await request(`/admin/v1/accounts/${item.id}/test`,"POST");reportType.value="account";reportTitle.value="资源账号检查结果";report.value=result.test_result;await load();message.value=result.test_result.connected?"连接测试完成，请核验权限后再启用账号。":"测试未通过，请检查配置。";});}
async function testSource(){await act(async()=>{if(!testingSource.value)return;reportType.value="generic";reportTitle.value="身份验证服务测试结果";report.value=await request(`/admin/v1/identity-sources/${testingSource.value.id}/test`,"POST",{credential:sourceCredential.value});sourceCredential.value="";testingSource.value=null;});}
async function preview(){await act(async()=>{reportType.value="preview";reportTitle.value="查看可用权限";report.value=await request("/admin/v1/access-preview","POST",{principal_id:previewPrincipal.value,scopes:previewScopes.value.split(",").map(s=>s.trim()).filter(Boolean)});});}
async function inspectExecution(item:Row){await act(async()=>{reportType.value="execution";reportTitle.value=`执行详情 · ${item.execution_id||item.id}`;report.value=await request(`/admin/v1/executions/${item.id}`);});}
async function inspectAudit(item:Row){await act(async()=>{reportType.value="audit";reportTitle.value=`审计详情 · #${item.id}`;report.value=await request(`/admin/v1/audit-events/${item.id}`);});}
async function cancelAdmin(item:Row){await act(async()=>{reportType.value="execution";reportTitle.value=`执行取消结果 · ${item.execution_id||item.id}`;report.value=await request(`/admin/v1/executions/${item.id}/cancel`,"POST");await refresh();});}
async function exportConfig(){await act(async()=>{configText.value=JSON.stringify(await request("/admin/v1/access-config/export"),null,2);message.value="配置已导出到下方，不含密码、私钥或 Key。";});}
async function checkImport(){await act(async()=>{importPreview.value=await request("/admin/v1/access-config/import-preview","POST",JSON.parse(configText.value));});}
async function applyImport(){await act(async()=>{await request("/admin/v1/access-config/import","POST",JSON.parse(configText.value));importPreview.value=null;await load();message.value="配置已更新";});}
function defaultOperation(){return activeAccess.value?.actions.find((a:string)=>a!=="db.schema")||"";}
function chooseResource(){sessionOpen.value=false;accessOption.value=activeResource.value?.access_modes[0]?.access_option_id||"";mode.value=activeResource.value?.default_mode||"normal";operation.value=defaultOperation();command.value=activeResource.value?.type==="ssh"?"id":"SELECT 1";kubeNamespace.value=activeResource.value?.namespaces?.[0]||"";kubeName.value="";userExecution.value=null;}
async function executeUser(){await act(async()=>{
  if(["ssh.connect","rdp.connect"].includes(operation.value)){sessionOpen.value=true;return;}
  const paths:Record<string,string>={"ssh.exec":"/v1/ssh/executions","ssh.inspect":"/v1/ssh/inspections","db.query":"/v1/db/queries","db.execute":"/v1/db/executions","k8s.read":"/v1/k8s/queries","k8s.restart":"/v1/k8s/restarts"};
  const path=paths[operation.value]; if(!path)throw new Error("请选择获准操作");
  const body:Row={resource_id:selectedResource.value,mode:mode.value,access_option_id:accessOption.value,idempotency_key:crypto.randomUUID()};
  if(operation.value==="ssh.inspect")body.check=sshCheck.value;
  else if(operation.value.startsWith("k8s."))Object.assign(body,{namespace:kubeNamespace.value,kind:operation.value==="k8s.restart"?"deployments":kubeOperation.value==="logs"?"pods":kubeOperation.value==="rollout"?"deployments":kubeKind.value,name:operation.value==="k8s.read"&&kubeOperation.value==="list"?"":kubeName.value,operation:operation.value==="k8s.restart"?"restart":kubeOperation.value});
  else body[operation.value==="ssh.exec"?"command":"sql"]=command.value;
  if(['ssh.exec','db.query','db.execute'].includes(operation.value))body.approval_reason=approvalReason.value;
  userExecution.value=await request(path,"POST",body,false);
  await loadUserHistory();
});}
async function pollUser(cursor:unknown=""){await act(async()=>{if(userExecution.value)userExecution.value=await request(`/v1/executions/${userExecution.value.execution_id}${typeof cursor==='string'&&cursor?'?cursor='+encodeURIComponent(cursor):''}`,"GET",undefined,false);});}
async function loadSchema(cursor=""){await act(async()=>{userSchema.value=await request(`/v1/resources/${selectedResource.value}/schema?mode=${mode.value}&access_option_id=${encodeURIComponent(accessOption.value)}&cursor=${encodeURIComponent(cursor)}`,"GET",undefined,false);});}
async function cancelUser(){await act(async()=>{if(userExecution.value)userExecution.value=await request(`/v1/executions/${userExecution.value.execution_id}/cancel`,"POST",{},false);});}
async function openOwnExecution(row:Row){await act(async()=>{
  selectedResource.value=row.resource_id;await nextTick();chooseResource();await nextTick();
  accessOption.value=activeResource.value?.access_modes.find((a:Row)=>a.mode===row.mode&&a.grant_id===row.grant_id)?.access_option_id||'';
  await nextTick();userExecution.value=await request('/v1/executions/'+row.id,'GET',undefined,false);
});}
const executionPoll=setInterval(async()=>{
  if(!isUser.value||!loggedIn.value||busy.value||!userExecution.value||!['pending_approval','queued','running','cancel_requested'].includes(userExecution.value.status))return;
  const id=userExecution.value.id;
  try{const result=await request('/v1/executions/'+id,'GET',undefined,false);if(userExecution.value?.id===id){userExecution.value=result;await loadUserHistory();}}
  catch(e){error.value=e instanceof Error?e.message:'刷新执行状态失败';}
},3000);
onUnmounted(()=>clearInterval(executionPoll));
async function logout(){await act(async()=>{if(isUser.value)await request("/v1/logout","POST",{},false);else sessionStorage.removeItem("deebee_token");loggedIn.value=false;newKey.value="";});}
function label(collection:string,id:string){const row=records.value[collection]?.find(r=>r.id===id);return row?.name||row?.username||id;}
function summary(item:Row){if(current.value==="identity-bindings")return `${label("identity-sources",item.source_id)} / ${item.subject} → ${label("principals",item.principal_id)}`;if(current.value==="access-grants")return `${label("principals",item.principal_id)} → ${label("resources",item.resource_id)} · 特权${item.allow_privileged?"允许":"关闭"}`;if(current.value==="accounts")return `${item.username} · ${optionLabel(item.tier)} · ${label("resources",item.resource_id)}`;if(current.value==="resources")return `${item.type} · ${item.host}:${item.port}${item.database?` / ${item.database}`:""}`;if(current.value==="identity-sources")return `${item.type} / ${item.validation_mode}`;if(current.value==="executions")return `${optionLabel(item.tool)} · ${optionLabel(item.mode)} · ${item.username}`;if(current.value==="audit-events")return `${item.actor_name||item.actor}（${item.actor_kind||"未知身份"}） · ${item.auth_method||"无认证"} · ${item.surface||"历史记录"}`;return optionLabel(item.kind)||item.subject||item.principal_id||"";}
function formatTime(value:number){return value?new Date(value*1000).toLocaleString():"—";}
onMounted(async()=>{loginSources.value=(await request("/access/login-options","GET",undefined,false).catch(()=>({sources:[]}))).sources;if(!isUser.value&&!authToken())return;await act(load);});
</script>

<template>
  <div class="access-workspace">
    <header class="access-header">
      <a href="#" class="access-brand" @click.prevent="location.href=location.pathname"><BrandMark /> DeeBee</a>
      <span>{{ isUser ? "我的资源" : "管理后台" }}</span>
      <span class="access-subtitle">外部身份 → 系统账号 → 用户组 → 资源账号</span>
      <a :href="isUser?'#access':'#access-user'" @click.prevent="location.href=location.pathname+(isUser?'#access':'#access-user');location.reload()">{{ isUser ? "管理后台" : "系统账号入口" }}</a>
      <a href="./">返回工作台</a><a-button v-if="loggedIn" @click="logout">退出</a-button>
    </header>
    <a-alert v-if="error" type="error" class="access-alert">{{ error }}</a-alert>
    <a-alert v-if="message" type="success" class="access-alert">{{ message }}</a-alert>
    <main v-if="!loggedIn" class="access-login">
      <Icon icon="lucide:shield-check" width="34" />
      <h1>{{ isUser ? "系统账号登录" : "管理员登录" }}</h1>
      <p>{{ isUser ? "使用系统账号密码或企业登录，查看已授权的资源。" : "使用工作台管理员账号进入管理后台。" }}</p>
      <form @submit.prevent="login">
        <div class="access-field">用户名<a-input v-model="loginName" :input-attrs="{'aria-label':'用户名'}" autocomplete="username" required /></div>
        <div class="access-field">密码<a-input v-model="loginPassword" :input-attrs="{'aria-label':'密码'}" type="password" autocomplete="current-password" required /></div>
        <a-button type="primary" html-type="submit" :disabled="busy">{{ busy?"正在验证…":"登录" }}</a-button>
      </form>
      <a v-for="source in isUser?loginSources:[]" :key="source.id" :href="API_BASE+'/auth/oidc/'+source.id+'/start'">使用 {{ source.name }} 登录</a>
    </main>
    <div v-else-if="!isUser" class="access-layout">
      <nav aria-label="管理后台导航">
        <a-menu :selected-keys="[navigationId]" @menu-item-click="navigate"><a-menu-item v-for="s in accessNavigation" :key="s.id" :disabled="busy"><template #icon><Icon :icon="s.icon" width="18" /></template>{{ s.name }}</a-menu-item></a-menu>
        <p>外部身份绑定系统账号<br>系统账号加入用户组后使用资源账号</p>
      </nav>
      <main ref="mainElement" class="access-main">
        <div v-if="current==='accounts'" class="access-breadcrumb"><a-button @click="navigate('resources')">资源管理</a-button><span> / {{ resourceDetail?.name }} / 资源详情</span></div>
        <div v-if="principalContext" class="access-breadcrumb"><a-button @click="navigate('principals')">系统账号</a-button><span> / {{ principalDetail?.name }}</span></div>
        <div class="access-heading">
          <div><h1>{{ current==='accounts' ? resourceDetail?.name+' · 资源详情' : section.name }}</h1><p>{{ section.description }}</p></div>
          <a-button v-if="section.fields&&!form&&!(current==='principals'&&principalContext)" type="primary" :disabled="busy" @click="start()">{{ current==='api-keys'?'添加 API Key':current==='accounts'?'添加资源账号':current==='identity-bindings'?'绑定外部身份':'新增' }}</a-button>
          <a-button :disabled="busy" @click="act(load)">刷新</a-button>
        </div>
        <div v-if="['identity-bindings','api-keys'].includes(current)" class="access-tabs" aria-label="外部身份功能">
          <a-button :class="{selected:current==='identity-bindings'}" @click="select('identity-bindings')">身份绑定</a-button>
          <a-button :class="{selected:current==='api-keys'}" @click="select('api-keys')">访问凭证（API Key）</a-button>
        </div>
        <div v-if="['integration','identity-sources'].includes(current)" class="access-tabs" aria-label="系统设置功能">
          <a-button :class="{selected:current==='integration'}" @click="select('integration')">接入与备份</a-button>
          <a-button :class="{selected:current==='identity-sources'}" @click="select('identity-sources')">身份验证服务</a-button>
        </div>
        <div v-if="['access-grants','preview'].includes(current)" class="access-tabs" aria-label="资源授权功能">
          <a-button :class="{selected:current==='access-grants'}" @click="select('access-grants')">授权关系</a-button>
          <a-button :class="{selected:current==='preview'}" @click="select('preview')">查看可用权限</a-button>
        </div>
        <section v-if="current==='accounts'" class="access-panel resource-context">
          <ResourceIcon :type="resourceDetail?.type||''" :size="36" /><span>{{ resourceProduct(resourceDetail?.type).name }} · {{ environmentLabel(resourceDetail?.environment) }}</span><span>{{ resourceDetail?.host }}:{{ resourceDetail?.port }} <template v-if="resourceDetail?.database"> / {{ resourceDetail.database }}</template></span>
          <span :class="['access-badge',resourceDetail?.enabled?'good':'muted']">{{ resourceDetail?.enabled?'资源已启用':'资源未启用' }}</span>
          <p v-if="resourceDetail?.connection_sync">来自工作台连接。{{ connectionNotice(resourceDetail) || '资源账号启用后，还需要在“资源授权”中分配给系统账号。' }}</p>
        </section>
        <section v-if="newKey" class="access-secret" role="status"><h2>API Key 仅显示一次</h2><p>请安全保存，不要发到聊天或写入日志。</p><a-input :input-attrs="{'aria-label':'新 API Key'}" :model-value="newKey" readonly /><a-button @click="newKey=''">已保存，隐藏原文</a-button></section>
        <SessionRecordings v-if="current==='session-recordings'" ref="recordingsPanel" />
        <ApprovalCenter v-else-if="current==='approvals'" ref="approvalsPanel" :request="request" :resources="records.resources||[]" :principals="records.principals||[]" />
        <form v-else-if="form" class="access-form" @submit.prevent="save">
          <h2>{{ editing?'编辑':'新增' }}{{ section.name }}</h2>
          <p v-if="editing?.connection_sync">来自工作台连接，地址和登录凭证请在工作台修改。{{ connectionNotice(editing) }}</p>
          <div v-if="current==='access-grants'" class="access-field">被授权对象<a-radio-group v-model="grantSubjectType" aria-label="被授权对象"><a-radio value="role">用户组</a-radio><a-radio value="principal">系统账号（直接授权）</a-radio></a-radio-group></div>
          <div class="access-fields">
            <div v-for="field in visibleFields" :key="field.key" class="access-field" :class="{'check-field':field.type==='check','wide-field':field.type==='textarea'||field.type==='json'}">
              <template v-if="field.type==='check'"><a-checkbox v-model="form[field.key]">{{ field.label }}</a-checkbox></template>
              <template v-else>
                <span>{{ field.label }}</span>
                <a-select v-if="field.type==='environment'" v-model="form[field.key]" role="combobox" allow-create allow-search allow-clear :options="[...new Set(['prod','uat','staging','test','dev',...(records.resources||[]).map(r=>r.environment).filter(Boolean)])]" :aria-label="field.label" />
                <a-select v-else-if="field.type==='tags'" v-model="form[field.key]" role="combobox" multiple allow-create allow-search allow-clear :options="classificationOptions(field.key)" :aria-label="field.label" />
                <a-select v-else-if="field.type==='select'" v-model="form[field.key]" role="combobox" :disabled="lockedField(field)" :required="field.required" :aria-label="field.label" @change="current==='access-grants'&&field.key==='resource_id'&&changedGrantResource()">
                  <a-option value="">请选择</a-option>
                  <a-option v-for="value in fieldOptions(field)" :key="value" :value="value">{{ optionLabel(value) }}</a-option>
                  <a-option v-for="item in options(field)" :key="item.id" :value="item.id">{{ item.name||item.subject||item.id }}{{ item.username?' · '+item.username:'' }}</a-option>
                </a-select>
                <JsonPanel v-else-if="field.type==='json'" v-model="form[field.key]" editable :label="field.label" /><a-textarea v-else-if="field.type==='textarea'" v-model="form[field.key]" :textarea-attrs="{'aria-label':field.label}" :auto-size="{minRows:3,maxRows:12}" :autocomplete="field.key==='private_key'?'off':undefined" />
                <a-input-number v-else-if="field.type==='number'" v-model="form[field.key]" :input-attrs="{'aria-label':field.label}" /><a-input v-else v-model="form[field.key]" :disabled="lockedField(field)" :input-attrs="{'aria-label':field.label}" :type="field.type==='password'?'password':'text'" :required="field.required" :autocomplete="field.type==='password'?'new-password':'off'" />
              </template>
              <small v-if="field.hint">{{ field.hint }}</small>
              <small v-if="lockedField(field)">身份命名空间保存后不可更改；更换类型、验证方式或 Issuer 请新增服务。</small>
            </div>
          </div>
          <template v-if="current==='access-grants'">
            <fieldset class="access-selection">
              <legend>资源账号</legend>
              <p v-if="!form.resource_id">先选择资源，再选择它下面的资源账号。</p>
              <p v-else-if="!grantChoices.length">该资源还没有资源账号，请先到资源管理中添加。</p>
              <div v-for="account in grantChoices" :key="account.id" class="access-field check-field">
                <a-checkbox :model-value="grantIds.includes(account.id)" :disabled="!account.enabled&&!grantIds.includes(account.id)" @change="checkArray(grantIds,account.id,$event)">
                  <span>{{ account.username }}</span> · {{ account.name }} · {{ accountPermissionLabel(account) }} · {{ account.enabled?'已启用':'未启用' }}
                </a-checkbox>
              </div>
              <small>每种权限类别最多选择一个资源账号。未启用的账号需先在资源管理中检查并启用。</small>
            </fieldset>
            <fieldset class="access-selection"><legend>允许操作</legend><AccessActions v-model="grantActions" :resource-type="grantResource?.type" /></fieldset>
            <p v-if="grantChoices.some(a=>grantIds.includes(a.id)&&a.tier==='privileged')" class="access-warning">选择了高权限资源账号。授权后可能修改数据或服务器文件；本次授权不会逐次询问管理员。</p>
            <div class="access-field">授权开始时间（留空为立即生效）<input v-model="grantStart" aria-label="授权开始时间" type="datetime-local" /></div>
            <div class="access-field">授权到期时间（留空为长期有效）<input v-model="grantExpiry" aria-label="授权到期时间" type="datetime-local" /></div>
            <details class="access-advanced">
              <summary>执行限制</summary><div class="access-fields">
                <div class="access-field">执行超时（秒）<a-input-number v-model="grantLimits.timeout_seconds" :input-attrs="{'aria-label':'执行超时（秒）'}" :min="1" :max="300" required /></div>
                <div class="access-field">最多返回行数<a-input-number v-model="grantLimits.max_rows" :input-attrs="{'aria-label':'最多返回行数'}" :min="1" :max="10000" required /></div>
                <div class="access-field">最多返回字节数<a-input-number v-model="grantLimits.max_output_bytes" :input-attrs="{'aria-label':'最多返回字节数'}" :min="1024" :max="2097152" required /></div>
              </div>
            </details>
          </template>
          <template v-if="current==='access-grants'&&!editing">
            <div class="access-field">同时授权其他资源<a-select v-model="batchResources" role="combobox" multiple allow-search aria-label="同时授权其他资源" :options="(records.resources||[]).filter(r=>r.id!==form?.resource_id).map(r=>({value:r.id,label:r.name}))" /></div>
            <fieldset v-for="id in batchResources.filter(id=>id!==form?.resource_id)" :key="id" class="access-selection">
              <legend>{{ label('resources',id) }}</legend>
              <div v-for="a in (records.accounts||[]).filter(a=>a.resource_id===id)" :key="a.id"><a-checkbox :model-value="(batchAccounts[id]||[]).includes(a.id)" :disabled="!a.enabled" @change="checkArray(batchAccounts[id]!,a.id,$event)">{{ a.username }} · {{ optionLabel(a.tier) }}</a-checkbox></div>
              <AccessActions v-model="batchActions[id]!" :resource-type="records.resources?.find(r=>r.id===id)?.type" />
            </fieldset><p v-if="batchResources.length">开始时间、到期时间及执行限制应用于本次所有资源。每个资源的账号与操作独立选择，整批保存。</p>
          </template>
          <template v-if="current==='api-keys'">
            <fieldset class="access-selection"><legend>允许操作</legend><AccessActions v-model="credentialActions" /></fieldset>
            <fieldset class="access-selection">
              <legend>限定可用资源（不选表示沿用系统账号的资源授权）</legend>
              <div v-for="resource in records.resources" :key="resource.id" class="access-field check-field"><a-checkbox :model-value="credentialResources.includes(resource.id)" @change="checkArray(credentialResources,resource.id,$event)">{{ resource.name }}</a-checkbox></div>
            </fieldset>
          </template>
          <div class="access-actions"><a-button type="primary" html-type="submit" :disabled="busy">{{ busy?'保存中…':'保存' }}</a-button><a-button html-type="button" @click="form=null">取消</a-button></div>
        </form>

        <section v-else-if="current==='principals'&&principalDetail" class="access-panel">
          <div class="access-heading"><div><h2>{{ principalDetail.name }}</h2><p>{{ optionLabel(principalDetail.kind) }} · {{ principalDetail.enabled?'已启用':'已停用' }}</p></div><a-button @click="start(principalDetail)">编辑系统账号</a-button></div>
          <RoleMemberships :records="records" :principal-id="principalDetail.id" :request="request" :reload="load" />
          <h3>外部身份</h3><p>这些外部身份绑定到当前系统账号。</p>
          <ul><li v-for="binding in principalRows('identity-bindings')" :key="binding.id">{{ label('identity-sources',binding.source_id) }} · {{ externalName(binding) }} · {{ binding.enabled?'已启用':'已停用' }}</li></ul>
          <p v-if="!principalRows('identity-bindings').length">尚未绑定外部身份。</p>
          <a-button @click="principalSection('identity-bindings')">管理外部身份</a-button>
          <h3>访问凭证</h3><ul><li v-for="key in principalRows('api-keys')" :key="key.id">{{ key.name }} · {{ key.enabled?'已启用':'已撤销' }} · 到期 {{ formatTime(key.expires_at) }}</li></ul>
          <a-button @click="principalSection('api-keys')">管理访问凭证</a-button>
          <h3>通过用户组继承的资源账号</h3><ul><li v-for="grant in inheritedGrants(principalDetail.id)" :key="grant.id">{{ subjectName(grant) }} → {{ label('resources',grant.resource_id) }} → {{ grantNames(grant) }}</li></ul><a-button @click="previewPrincipal=principalDetail.id;previewScopes=actionOptions.map(a=>a.value).join(',');preview()">查看最终可用权限</a-button>
          <h3>直接授权的资源账号</h3><ul><li v-for="grant in principalRows('access-grants')" :key="grant.id">{{ label('resources',grant.resource_id) }} → {{ grantNames(grant) }} · {{ grant.enabled?'已授权':'已停用' }}</li></ul>
          <p v-if="!principalRows('access-grants').length">尚未授权任何资源账号。</p><a-button @click="principalSection('access-grants')">管理资源授权</a-button>
        </section>

        <RoleManager v-else-if="current==='roles'" :records="records" :request="request" :reload="load" @edit="start" @grant="addRoleGrant" @edit-grant="editRoleGrant" />

        <ResourceCatalog v-else-if="current==='resources'" :resources="rows" :busy="busy" @edit="start" @detail="openAccounts" @toggle="toggle" @remove="askDelete" />

        <div v-else-if="current==='access-grants'" class="access-table-wrap">
          <a-table :key="current" :data="rows" row-key="id" :pagination="{pageSize:20,showTotal:true}" :scroll="{x:1000}" :loading="busy">
            <template #columns>
              <a-table-column title="被授权对象"><template #cell="{record:grant}">{{ subjectName(grant) }}<small>{{ grantedPeople(grant) }}</small></template></a-table-column>
              <a-table-column title="资源"><template #cell="{record:grant}">{{ label('resources',grant.resource_id) }}</template></a-table-column>
              <a-table-column title="资源账号"><template #cell="{record:grant}">{{ grantNames(grant) }}</template></a-table-column>
              <a-table-column title="允许操作 / 有效期"><template #cell="{record:grant}">{{ grant.actions.map((a:string)=>actionOptions.find(o=>o.value===a)?.label||a).join('、') }}<small>{{ grant.expires_at?'到期 '+formatTime(grant.expires_at):'长期有效' }}</small></template></a-table-column>
              <a-table-column title="状态"><template #cell="{record:grant}">{{ !grant.enabled?'已停用':grant.starts_at&&grant.starts_at*1000>Date.now()?'未生效':grant.expires_at&&grant.expires_at*1000<Date.now()?'已到期':'已授权' }}</template></a-table-column>
              <a-table-column title="操作"><template #cell="{record:grant}"><div class="access-row-actions"><a-button @click="start(grant)">修改授权</a-button><a-button :disabled="busy" @click="toggle(grant)">{{ grant.enabled?'停用授权':'启用授权' }}</a-button></div></template></a-table-column>
            </template>
          </a-table>
        </div>

        <section v-else-if="current==='preview'" class="access-panel">
          <div class="access-field">系统账号<a-select v-model="previewPrincipal" role="combobox" aria-label="系统账号"><a-option value="">选择系统账号</a-option><a-option v-for="p in records.principals" :key="p.id" :value="p.id">{{ p.name }}</a-option></a-select></div>
          <details class="access-advanced"><summary>模拟访问凭证的权限范围</summary><p>用于排查凭证限制，填写协议权限名称；不会修改任何授权。</p><a-input v-model="previewScopes" :input-attrs="{'aria-label':'模拟凭证权限'}" /></details>
          <a-button type="primary" :disabled="busy||!previewPrincipal" @click="preview">查看可用资源账号</a-button>
        </section>

        <section v-else-if="current==='integration'" class="access-panel">
          <h2>程序接入地址</h2><div class="access-field">MCP 地址<a-input :model-value="status.mcp_url" :input-attrs="{'aria-label':'MCP 地址'}" readonly /></div><div class="access-field">REST API 地址<a-input :model-value="status.api_url" :input-attrs="{'aria-label':'REST API 地址'}" readonly /></div>
          <p>外部身份先绑定系统账号，系统账号再通过资源授权使用资源账号。</p>
          <details class="access-advanced"><summary>开发者接入说明</summary><p>API Key 使用 X-DeeBee-API-Key；外部验证服务另传 X-DeeBee-Identity-Source。企业登录使用 Authorization: Bearer &lt;Access Token&gt;，不能同时提交两种凭证。</p><p>高权限资源账号必须明确请求 mode: privileged，且凭证与资源授权均允许 privilege:use。默认请求不会自动升级权限。</p><p>远程桌面通过系统账号入口使用，需单独授权“连接远程桌面”；MCP 不提供桌面操作。其他尚未支持的资源类型用于登记。</p></details>
          <hr><h2>配置备份与导入</h2><p>导出不含密码、私钥或 API Key 原文。导入不会自动启用新资源账号。</p>
          <a-button :disabled="busy" @click="exportConfig">导出配置</a-button><JsonPanel v-model="configText" editable label="配置 JSON" />
          <div class="access-actions"><a-button :disabled="busy||!configText" @click="checkImport">检查导入内容</a-button><a-button v-if="importPreview" :disabled="busy" @click="applyImport">确认导入</a-button></div><JsonPanel v-if="importPreview" :value="importPreview" />
          <details class="access-advanced"><summary>连接同步维护</summary><p>工作台连接会自动同步到资源管理。以下兼容操作会重置检查结果，不会新增授权。</p><a-button :disabled="busy" @click="previewLegacy">查看工作台连接</a-button><div v-for="item in legacyItems" :key="item.id" class="access-panel"><span>{{ item.name }} · {{ item.type }}</span><p>{{ item.host }}:{{ item.port }} · {{ item.username }}</p><p>{{ item.reason }}</p><a-button :disabled="busy||!item.supported" @click="importLegacy(item)">重新同步并停用</a-button></div></details>
        </section>

        <template v-else>
          <div v-if="current==='principals'" class="access-field">搜索系统账号<a-input v-model="principalSearch" :input-attrs="{'aria-label':'搜索系统账号'}" /></div>
          <section v-if="current==='audit-events'" class="access-filters"><div class="access-field">账号 / 外部身份<a-input v-model="auditActor" :input-attrs="{'aria-label':'账号 / 外部身份'}" placeholder="名称或标识" /></div><div class="access-field">操作<a-input v-model="auditOperation" :input-attrs="{'aria-label':'操作名称'}" placeholder="操作名称" /></div><div class="access-field">入口<a-select v-model="auditSurface" role="combobox" aria-label="入口"><a-option value="">全部入口</a-option><a-option value="mcp">MCP</a-option><a-option value="access_ui">系统账号入口</a-option><a-option value="admin">管理后台</a-option><a-option value="workbench">工作台</a-option><a-option value="execution_worker">执行服务</a-option><a-option value="control_plane">配置变更</a-option><a-option value="legacy_audit">历史记录</a-option></a-select></div><a-button :disabled="busy" @click="act(refresh)">查询</a-button></section>
          <div class="access-table-wrap">
            <a-table :key="current" :data="rows" row-key="id" :pagination="{pageSize:20,showTotal:true}" :scroll="{x:1000}" :loading="busy">
              <template #columns>
                <a-table-column :title="current==='accounts'?'资源账号':current==='principals'?'系统账号':current==='identity-bindings'?'外部身份':'名称'"><template #cell="{record:item}"><span>{{ current==='accounts'?item.username:current==='audit-events'?(item.operation||item.action):(current==='identity-bindings'?externalName(item):(item.name||item.action||item.execution_id||item.subject||label('principals',item.principal_id))) }}</span><small v-if="current==='accounts'">{{ item.name }}</small><details><summary>查看标识</summary><small>{{ current==='audit-events'?item.request_id:item.id }}<span v-if="current==='identity-bindings'"> · {{ item.subject }}</span></small></details></template></a-table-column>
                <a-table-column :title="current==='identity-bindings'?'绑定的系统账号':current==='accounts'?'权限与授权关系':'说明'">
                  <template #cell="{record:item}">
                    <template v-if="current==='identity-bindings'">{{ label('principals',item.principal_id) }}<small>{{ label('identity-sources',item.source_id) }}</small></template>
                    <template v-else-if="current==='accounts'">{{ optionLabel(item.tier) }} · {{ accountHealth(item) }}<small>已授权给：{{ accountGrants(item.id).filter(effective).map(g=>subjectName(g)+'：'+grantedPeople(g)).join('、')||'暂无系统账号' }}</small></template>
                    <template v-else-if="current==='api-keys'">绑定：{{ label('principals',item.principal_id) }}<small>到期：{{ formatTime(item.expires_at) }}</small><small>{{ item.scopes.map((a:string)=>actionOptions.find(o=>o.value===a)?.label||a).join('、') }}</small></template>
                    <template v-else>{{ summary(item) }}</template>
                    <small v-if="current==='audit-events'">{{ item.method }} {{ item.path }}</small>
                  </template>
                </a-table-column>
                <a-table-column title="状态"><template #cell="{record:item}"><span :class="['access-badge',item.enabled||item.status==='succeeded'?'good':'muted']">{{ current==='audit-events'?(item.status_code||'—')+' · '+(item.duration_ms||0)+' ms':current==='api-keys'&&item.expires_at*1000<Date.now()?'已到期':item.status?optionLabel(item.status):item.enabled===undefined?'已记录':item.enabled?'已启用':'已停用' }}</span><small v-if="current==='audit-events'">{{ formatTime(item.at) }}</small></template></a-table-column>
                <a-table-column title="操作">
                  <template #cell="{record:item}">
                    <div class="access-row-actions">
                      <a-button v-if="current==='principals'" @click="openPrincipal(item)">查看关联</a-button>
                      <a-button v-if="section.fields&&current!=='api-keys'" @click="start(item)">{{ current==='identity-bindings'?'修改绑定':'编辑' }}</a-button>
                      <a-button v-if="section.fields&&(current!=='api-keys'||item.enabled)" :disabled="busy||(!item.enabled&&item.connection_sync&&item.connection_sync.state!=='synced')" @click="toggle(item)">{{ current==='api-keys'?'撤销凭证':item.enabled?'停用':'启用' }}</a-button>
                      <a-button v-if="current==='accounts'" :disabled="busy" @click="testAccount(item)">检查连接与权限</a-button>
                      <a-button v-if="current==='identity-sources'" @click="testingSource=item;sourceCredential=''">检查验证服务</a-button>
                      <a-button v-if="current==='executions'" @click="inspectExecution(item)">查看结果</a-button>
                      <a-button v-if="current==='executions'&&!['succeeded','failed','cancelled','unknown','rejected','expired'].includes(item.status)" @click="cancelAdmin(item)">请求取消</a-button>
                      <a-button v-if="current==='audit-events'" @click="inspectAudit(item)">查看详情</a-button>
                    </div>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>
        </template>
        <section v-if="testingSource" class="access-panel"><h2>检查 {{ testingSource.name }}</h2><div class="access-field">测试凭证（可选，不保存）<a-input v-model="sourceCredential" :input-attrs="{'aria-label':'测试凭证'}" type="password" autocomplete="off" /></div><p>企业登录服务留空可检查服务配置；验证外部身份绑定需提供访问令牌。</p><a-button :disabled="busy" @click="testSource">检查</a-button><a-button @click="testingSource=null;sourceCredential=''">取消</a-button></section>
      </main>
    </div>
    <main v-else class="access-user-main">
      <h1>{{ me.principal?.name }} 的可用资源</h1><p>所属用户组：{{ me.roles?.map((r:Row)=>r.name).join('、')||'暂无用户组' }}</p><a-button :disabled="busy" @click="act(load)">刷新可用资源</a-button><p>选择资源查看可用操作和连接方式。</p>
      <div class="access-resource-cards"><a-button v-for="r in userResources" :key="r.id" :class="{selected:selectedResource===r.id}" @click="selectedResource=r.id;chooseResource()"><ResourceIcon :type="r.type" :size="32" /><span>{{ r.name }}</span><span>{{ resourceProduct(r.type).name }} {{ r.database }}</span><small>{{ environmentLabel(r.environment) }}</small></a-button></div>
      <p v-if="!userResources.length" class="access-empty">暂无可用资源账号，请联系管理员配置资源授权。</p>
      <section v-if="activeResource" class="access-panel">
        <h2>{{ activeResource.name }}</h2><div class="access-field">资源账号<a-select v-model="accessOption" role="combobox" aria-label="资源账号"><a-option v-for="m in activeResource.access_modes" :key="m.access_option_id" :value="m.access_option_id">{{ m.username }} · {{ optionLabel(m.mode) }} · {{ m.role_name||'直接授权' }}</a-option></a-select></div>
        <p>权限来源：{{ activeAccess?.role_name||'直接授权' }} · 到期 {{ formatTime(activeAccess?.expires_at) }}</p>
        <a-alert v-if="activeAccess?.approval_controlled" type="info">此资源已启用操作审批，命中规则的请求将在管理员通过后执行。{{ activeResource.type==='ssh'?'请逐条提交服务器命令；固定只读检查可直接执行。':'' }}</a-alert>
        <a-button v-if="activeAccess?.actions.includes('db.schema')" :disabled="busy" @click="loadSchema()">查看数据库结构</a-button><JsonPanel v-if="userSchema" :value="userSchema" /><a-button v-if="userSchema?.next_cursor" :disabled="busy" @click="loadSchema(userSchema.next_cursor)">下一页</a-button>
        <div class="access-field">操作<a-select v-model="operation" role="combobox" aria-label="操作"><a-option v-for="action in activeAccess?.actions.filter((a:string)=>a!=='db.schema')" :key="action" :value="action">{{ optionLabel(action) }}</a-option></a-select></div>
        <div v-if="operation==='ssh.inspect'" class="access-field">检查项目<a-select v-model="sshCheck" role="combobox" aria-label="检查项目"><a-option v-for="c in ['overview','disk','memory','processes','network']" :key="c" :value="c">{{ ({overview:'概览',disk:'磁盘与 inode',memory:'内存',processes:'进程',network:'网络'})[c] }}</a-option></a-select></div>
        <template v-else-if="activeResource.type==='k8s'">
          <div class="access-field">Namespace<a-select v-model="kubeNamespace" role="combobox" aria-label="Namespace"><a-option v-for="n in activeResource.namespaces" :key="n" :value="n">{{ n }}</a-option></a-select></div>
          <template v-if="operation==='k8s.read'"><div class="access-field">查询类型<a-select v-model="kubeOperation" role="combobox" aria-label="查询类型"><a-option value="list">列表</a-option><a-option value="get">详情</a-option><a-option value="logs">Pod 日志</a-option><a-option value="rollout">等待 Deployment 就绪</a-option></a-select></div><div class="access-field">资源类型<a-select v-model="kubeKind" role="combobox" aria-label="资源类型"><a-option v-for="k in ['pods','deployments','events','services','replicasets']" :key="k" :value="k">{{ k }}</a-option></a-select></div></template>
          <div v-if="operation==='k8s.restart'||kubeOperation!=='list'" class="access-field">{{ operation==='k8s.restart'?'Deployment 名称':'资源名称' }}<a-input v-model="kubeName" /></div>
        </template>
        <div v-else-if="!['ssh.connect','rdp.connect'].includes(operation)" class="access-field">{{ activeResource.type==='ssh'?'服务器命令':'SQL' }}<a-textarea v-model="command" :textarea-attrs="{'aria-label':activeResource.type==='ssh'?'服务器命令':'SQL'}" :auto-size="{minRows:6,maxRows:20}" /></div>
        <div v-if="['ssh.exec','db.query','db.execute'].includes(operation)" class="access-field">申请原因（供管理员审核）<a-textarea v-model="approvalReason" :textarea-attrs="{'aria-label':'申请原因'}" :max-length="2000" :auto-size="{minRows:2,maxRows:4}" placeholder="说明操作目的、影响范围和验证方式" /></div>
        <p v-if="mode==='privileged'" class="access-warning">正在使用已授权的高权限资源账号，操作可能修改数据或服务器文件。</p>
        <a-button type="primary" :disabled="busy||!operation" @click="executeUser">{{ ['ssh.connect','rdp.connect'].includes(operation)?'打开会话':'提交执行' }}</a-button>
        <section v-if="sessionOpen" class="access-live-session"><a-button @click="sessionOpen=false">关闭会话</a-button><SshTerminal v-if="activeResource.type==='ssh'" :key="accessOption" profile-id="" :title="activeResource.name" active :access="sessionAccess" /><RdpDesktop v-else-if="activeResource.type==='rdp'" :key="accessOption" profile-id="" :title="activeResource.name" active :access="sessionAccess" /></section>
        <div v-if="userExecution" class="access-panel"><span>{{ optionLabel(userExecution.status) }}</span><a-button :disabled="busy" @click="pollUser">刷新结果</a-button><a-button v-if="userExecution.result?.next_cursor" :disabled="busy" @click="pollUser(userExecution.result.next_cursor)">下一页结果</a-button><a-button v-if="!['succeeded','failed','cancelled','unknown','rejected','expired'].includes(userExecution.status)" :disabled="busy" @click="cancelUser">请求取消</a-button><a-alert v-if="userExecution.status==='pending_approval'" type="warning">申请已提交，原命令尚未执行。请等待管理员审核，到期时间：{{ formatTime(userExecution.approval.expires_at) }}。请勿重复提交。</a-alert><p v-if="userExecution.approval">审批规则：{{ userExecution.approval.rules.map((r:Row)=>r.name).join('、') }}</p><p v-if="userExecution.approval?.comment">审核意见：{{ userExecution.approval.comment }}</p><a-alert v-if="userExecution.error" type="warning">{{ userExecution.error.message }}</a-alert><JsonPanel v-if="userExecution.result" :value="userExecution.result" /><details><summary>执行详情</summary><JsonPanel :value="userExecution" /></details></div>
      </section>
      <section class="access-panel"><h2>我的执行与审批申请</h2><a-button :disabled="busy" @click="act(loadUserHistory)">刷新我的申请</a-button><p v-if="!userHistory.length">刷新后可查看最近 100 条申请，重新登录后也可继续查看。</p><a-table :data="userHistory" row-key="id" :pagination="{pageSize:10}"><template #columns><a-table-column title="资源"><template #cell="{record:r}">{{ userResources.find(item=>item.id===r.resource_id)?.name||r.resource_id }}</template></a-table-column><a-table-column title="操作 / 时间"><template #cell="{record:r}">{{ optionLabel(r.tool) }} · {{ formatTime(r.created_at) }}</template></a-table-column><a-table-column title="状态"><template #cell="{record:r}">{{ optionLabel(r.status) }}</template></a-table-column><a-table-column title="操作"><template #cell="{record:r}"><a-button :disabled="busy" @click="openOwnExecution(r)">查看执行结果</a-button></template></a-table-column></template></a-table></section>
    </main>
    <a-modal :visible="!!report" :title="reportTitle||'详情'" :width="960" :footer="false" @cancel="report=null">
      <div v-if="report" class="report-content">
        <template v-if="reportType==='account'"><p>连接：{{ report.connected?'成功':'未通过' }}</p><p>普通账号安全检查：{{ report.normal_safe?'通过':'未通过或不适用' }}</p><p>连接成功不代表账号只有只读权限。请核实检查详情后再启用。</p></template>
        <template v-else-if="reportType==='preview'"><p v-if="!report.resources?.length">该系统账号在所选凭证权限范围内，没有可用资源账号。</p><article v-for="resource in report.resources" :key="resource.id" class="access-panel"><h3>{{ resource.name }}</h3><p v-for="account in resource.access_modes" :key="account.access_option_id">{{ account.username }} · {{ optionLabel(account.mode) }} · {{ account.role_name||'直接授权' }} · {{ account.actions.map(optionLabel).join('、') }}</p></article></template>
        <template v-else-if="reportType==='audit'"><dl class="access-audit-grid"><div><dt>操作人</dt><dd>{{ report.actor_name||report.actor }}</dd></div><div><dt>外部身份 / 登录方式</dt><dd>{{ report.source_id }} · {{ report.subject }} · {{ report.auth_method }}</dd></div><div><dt>入口与操作</dt><dd>{{ report.surface }} · {{ report.operation }}</dd></div><div><dt>资源账号</dt><dd>{{ report.account_username||'—' }} · {{ optionLabel(report.mode) }}</dd></div><div><dt>请求</dt><dd>{{ report.method }} {{ report.path }}</dd></div><div><dt>结果</dt><dd>{{ report.status_code }} · {{ report.error_code||'成功' }}</dd></div></dl><h3>请求内容（已脱敏）</h3><JsonPanel :value="report.request_preview" :truncated="report.request_truncated" label="请求 · 上限 1 KiB" /><h3>返回内容（已脱敏）</h3><JsonPanel :value="report.response_preview" :truncated="report.response_truncated" label="返回 · 上限 4 KiB" /></template>
        <template v-else-if="reportType==='execution'"><p>执行状态：{{ optionLabel(report.status) }}</p><p>资源账号：{{ report.account?.username||report.username||'—' }}</p><p>权限来源：{{ report.role_name||'直接授权' }} · {{ report.grant_id }}</p><JsonPanel :value="report.result" /></template>
        <details :open="reportType==='generic'"><summary>技术详情</summary><JsonPanel :value="report" /></details>
      </div>
    </a-modal>
    <a-modal :visible="!!deleting" title="删除资源" :ok-loading="busy" :ok-button-props="{status:'danger',disabled:deleteName!==deleting?.name}" ok-text="确认删除" @ok="removeResource" @cancel="deleting=null">
      <template v-if="deleting"><a-alert type="warning">从资源管理删除，并撤销关联资源账号及授权。工作台连接和远端数据不删除，历史审计保留；此连接不会再自动入池。</a-alert><p>请输入资源名称确认：{{ deleting.name }}</p><a-input v-model="deleteName" :input-attrs="{'aria-label':'确认删除的资源名称'}" /><a-alert v-if="error" type="error">{{ error }}</a-alert></template>
    </a-modal>
  </div>
</template>

<style>
html,body,#app{margin:0;width:100%;height:100%;font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;color:var(--color-text-1)}
*{box-sizing:border-box}.arco-modal-title,.arco-table-th,.arco-card-header-title,.arco-menu-item{font-weight:400!important}.arco-modal{max-width:calc(100vw - 32px)}.arco-modal-body{max-height:75vh;overflow:auto}
</style>
<style scoped>
.access-live-session{height:600px;display:flex;flex-direction:column;margin-top:16px}.access-live-session>.remote-session{flex:1;min-height:0}.access-workspace{height:100dvh;display:flex;flex-direction:column;overflow:hidden;background:var(--color-fill-2);font-size:14px}.access-header{height:56px;flex-shrink:0;display:flex;align-items:center;gap:24px;padding:0 24px;background:white;border-bottom:1px solid var(--color-border-2)}.access-header a{text-decoration:none;color:var(--color-text-2)}.access-brand{display:flex;gap:10px;align-items:center;font-size:18px}.access-brand>span{display:grid;place-items:center;width:30px;height:30px;background:rgb(var(--primary-6));color:white;border-radius:6px}.access-subtitle{flex:1;font-size:12px;color:var(--color-text-3)}.access-layout{display:grid;grid-template-columns:208px minmax(0,1fr);flex:1;min-height:0;overflow:hidden}.access-layout nav{overflow:auto;background:var(--color-bg-1);border-right:1px solid var(--color-border-2);padding:14px 8px;display:flex;flex-direction:column}.access-layout nav p{margin-top:auto;padding:20px 14px;font-size:12px;color:var(--color-text-3)}.access-main{min-width:0;overflow:auto;padding:24px 28px;scrollbar-gutter:stable}.access-heading{display:flex;align-items:flex-start;gap:12px;margin-bottom:22px}.access-heading>div{flex:1}.access-heading p{margin-bottom:0}.access-workspace h1{font-size:18px;font-weight:500;margin:0 0 8px}.access-workspace h2{font-size:16px;font-weight:500;margin:0 0 14px}.access-workspace h3{font-size:14px;font-weight:400;margin:20px 0 12px}.access-workspace p,.report-content p{line-height:1.7;color:var(--color-text-3)}.access-workspace small{font-size:12px;color:var(--color-text-3)}.access-panel,.access-form{padding:22px;background:white;border:1px solid var(--color-border-2);border-radius:4px;margin:16px 0}.access-panel>.access-field{margin:12px 0}.access-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.access-workspace .access-field{display:flex;flex-direction:column;gap:8px;font-weight:400}.access-workspace .check-field{flex-direction:row;align-items:center;flex-wrap:wrap}.check-field small{flex-basis:100%}.wide-field{grid-column:1/-1}.access-actions,.access-row-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.access-actions{margin-top:22px;padding-top:16px;border-top:1px solid var(--color-border-2)}.access-row-actions :deep(.arco-btn){font-size:12px}.access-tabs,.access-breadcrumb{display:flex;gap:8px;align-items:center;margin-bottom:18px}.access-tabs .selected{color:rgb(var(--primary-6));background:var(--color-primary-light-1)}.access-alert{margin:8px 24px;flex-shrink:0;width:auto}.access-selection{border:1px solid var(--color-border-2);border-radius:4px;margin:22px 0;padding:18px}.access-selection legend{font-weight:400;padding:0 6px}.access-selection>.access-field{margin:10px 0}.access-advanced{margin:20px 0}.access-advanced .access-fields{margin-top:16px}.access-workspace summary{cursor:pointer;color:rgb(var(--primary-6));font-size:12px}.access-table-wrap{min-width:0;overflow:hidden}.access-table-wrap :deep(td){overflow-wrap:anywhere}.access-table-wrap small{display:block;margin-top:6px}.access-table-wrap details{margin-top:8px}.access-filters{display:grid;grid-template-columns:repeat(3,1fr) auto;gap:12px;align-items:end}.access-badge{display:inline-block;font-size:12px;padding:3px 8px;background:var(--color-fill-2);color:var(--color-text-3);border-radius:3px;white-space:nowrap}.access-badge.good{background:rgb(var(--green-1));color:rgb(var(--green-6))}.access-login{width:420px;max-width:calc(100% - 32px);margin:70px auto;padding:28px;background:white;border:1px solid var(--color-border-2)}.access-login form{display:grid;gap:18px}.access-login h1{margin-top:18px}.access-login>a{display:block;margin-top:18px}.access-user-main{overflow:auto;padding:28px;width:100%;max-width:1200px;margin:auto}.access-resource-cards{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.access-resource-cards :deep(.arco-btn){height:auto;padding:16px;display:flex;flex-direction:column;gap:8px}.access-secret,.access-warning{background:rgb(var(--orange-1));padding:16px;border-radius:4px}.access-secret{margin:16px 0}.access-secret .arco-input-wrapper{margin:12px 0}.access-empty{text-align:center;padding:30px}.access-audit-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--color-border-2);border:1px solid var(--color-border-2)}.access-audit-grid>div{padding:12px;background:var(--color-fill-1)}.access-audit-grid dt{font-size:12px;color:var(--color-text-3);margin-bottom:8px}.access-audit-grid dd{margin:0;overflow-wrap:anywhere}.report-content h3{font-size:14px;font-weight:400}.resource-context{font-size:12px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}.resource-context p{flex-basis:100%;margin:0}.access-resource-cards :deep(.arco-btn.selected){border-color:rgb(var(--primary-5));background:var(--color-primary-light-1)}.access-workspace input[type=datetime-local]{font:inherit;border:1px solid var(--color-border-2);padding:8px;background:var(--color-fill-2)}
@media(max-width:900px){.access-layout{grid-template-columns:176px minmax(0,1fr)}.access-main{padding:20px}.access-subtitle{display:none}.access-header{gap:14px}.access-fields,.access-filters{grid-template-columns:1fr 1fr}}@media(max-width:600px){.access-layout{grid-template-columns:136px minmax(0,1fr)}.access-main{padding:12px}.access-header{padding:0 12px;gap:10px}.access-header>a:not(.access-brand){font-size:12px}.access-header>span{display:none}.access-heading{flex-wrap:wrap}.access-fields,.access-filters{grid-template-columns:1fr}.access-panel,.access-form{padding:14px}.access-audit-grid{grid-template-columns:1fr}}
</style>
