<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, reactive } from "vue";
import type { Database, DatabaseSchema, DbObject, Objects, Profile } from "../types";

const props = defineProps<{
  profile: Profile; serverExpanded: boolean; databases: Database[];
  schemas: Record<string, DatabaseSchema[]>; selected: string; selectedSchema: string;
  expanded: Set<string>; expandedSchemas: Set<string>; objects: Record<string, Objects>; filter: string; rootless?: boolean;
}>();
const emit = defineEmits<{
  toggleServer: []; serverMenu: [event: MouseEvent]; selectDatabase: [database: string]; toggleDatabase: [database: string];
  selectSchema: [database: string, schema: string]; toggleSchema: [database: string, schema: string];
  openTable: [table: string, database: string, schema: string]; designTable: [table: string, database: string, schema: string];
  openList: [kind: keyof Objects, database: string, schema: string]; openObject: [kind: string, object: DbObject, database: string, schema: string];
  databaseMenu: [event: MouseEvent, database: Database]; tableMenu: [event: MouseEvent, table: DbObject, database: string, schema: string];
  objectMenu: [event: MouseEvent, kind: string, object: DbObject, database: string, schema: string]; inspect: [object: DbObject, database: string, schema: string];
}>();

const categoryOpen = reactive<Record<string, boolean>>({});
const isPostgres = computed(() => props.profile.driver === "postgresql");
const isMssql = computed(() => props.profile.driver === "mssql");
const usesSchemas = computed(() => isPostgres.value || isMssql.value);
const driverName = computed(() => isPostgres.value ? "PostgreSQL" : isMssql.value ? "SQL Server" : "MySQL");
const definitions = computed(() => [
  { key: "tables" as const, label: "表", icon: "lucide:table-2" },
  { key: "views" as const, label: "视图", icon: "lucide:panels-top-left" },
  { key: "routines" as const, label: "函数与过程", icon: "lucide:function-square" },
  { key: "triggers" as const, label: "触发器", icon: "lucide:zap" },
  ...(!usesSchemas.value ? [{ key: "events" as const, label: "事件", icon: "lucide:clock-3" }] : []),
]);
const visibleDatabases = computed(() => props.databases.filter(db => !props.filter || db.name.toLowerCase().includes(props.filter.toLowerCase()) || props.expanded.has(db.name)));
function contextKey(database: string, schema = "") { return schema ? `${database}:${schema}` : database; }
function schemaKey(database: string, schema: string) { return `${database}:${schema}`; }
function categoryKey(database: string, schema: string, kind: string) { return `${contextKey(database, schema)}:${kind}`; }
function isCategoryOpen(database: string, schema: string, kind: string) { return categoryOpen[categoryKey(database, schema, kind)] !== false; }
function toggleCategory(database: string, schema: string, kind: keyof Objects) { const key = categoryKey(database, schema, kind); categoryOpen[key] = !isCategoryOpen(database, schema, kind); emit("openList", kind, database, schema); }
function iconFor(kind: keyof Objects) { return definitions.value.find(item => item.key === kind)?.icon || "lucide:box"; }
function filtered(items: DbObject[] = []) { return items.filter(item => !props.filter || item.name.toLowerCase().includes(props.filter.toLowerCase())); }
function contextObjects(database: string, schema = "") { return props.objects[contextKey(database, schema)]; }
</script>

<template>
  <div :class="rootless ? 'tree-children' : 'tree'" :role="rootless ? 'group' : 'tree'" :aria-label="rootless ? undefined : '数据库对象树'">
    <button v-if="!rootless" class="tree-node server-node" role="treeitem" :aria-label="`${serverExpanded ? '收起' : '展开'}服务器 ${profile.name}`" :aria-expanded="serverExpanded" @click="emit('toggleServer')" @contextmenu.prevent="emit('serverMenu', $event)">
      <span class="tree-indent" /><span class="twisty"><Icon :icon="serverExpanded ? 'lucide:chevron-down' : 'lucide:chevron-right'" /></span><Icon icon="lucide:server" class="node-icon server-icon" />
      <span class="node-label"><b>{{ profile.name }}</b><small>{{ profile.host }}:{{ profile.port }}</small></span><span class="server-driver">{{ driverName }}</span>
    </button>
    <template v-for="db in rootless || serverExpanded ? visibleDatabases : []" :key="db.name">
      <button class="tree-node db-node" :class="{ active: selected === db.name && (!usesSchemas || !selectedSchema) }" role="treeitem" :aria-label="`${expanded.has(db.name) ? '收起' : '展开'} ${db.name}`" :aria-expanded="expanded.has(db.name)" @click="emit('selectDatabase', db.name)" @contextmenu.prevent="emit('databaseMenu', $event, db)">
        <span class="tree-indent level-one" /><span class="twisty" @click.stop="emit('toggleDatabase', db.name)"><Icon :icon="expanded.has(db.name) ? 'lucide:chevron-down' : 'lucide:chevron-right'" /></span><Icon icon="lucide:database" class="node-icon database-icon" /><span class="node-label">{{ db.name }}</span>
      </button>
      <template v-if="expanded.has(db.name)">
        <template v-if="usesSchemas">
          <div v-if="!schemas[db.name]" class="tree-loading">正在读取 Schema…</div>
          <template v-for="itemSchema in schemas[db.name] || []" v-else :key="schemaKey(db.name,itemSchema.name)">
            <button class="tree-node schema-node" :class="{active:selected===db.name && selectedSchema===itemSchema.name}" role="treeitem" :aria-expanded="expandedSchemas.has(schemaKey(db.name,itemSchema.name))" @click="emit('selectSchema',db.name,itemSchema.name)">
              <span class="tree-indent level-two" /><span class="twisty" @click.stop="emit('toggleSchema',db.name,itemSchema.name)"><Icon :icon="expandedSchemas.has(schemaKey(db.name,itemSchema.name))?'lucide:chevron-down':'lucide:chevron-right'" /></span><Icon icon="lucide:boxes" class="node-icon schema-icon" /><span class="node-label">{{ itemSchema.name }}</span><span v-if="itemSchema.system" class="schema-system">系统</span>
            </button>
            <template v-if="expandedSchemas.has(schemaKey(db.name,itemSchema.name))">
              <div v-if="!contextObjects(db.name,itemSchema.name)" class="tree-loading schema-loading">正在读取对象…</div>
              <template v-for="category in definitions" v-else :key="category.key">
                <button class="tree-node category-node schema-category-node" role="treeitem" :aria-expanded="isCategoryOpen(db.name,itemSchema.name,category.key)" @click="toggleCategory(db.name,itemSchema.name,category.key)">
                  <span class="tree-indent level-three" /><span class="twisty"><Icon :icon="isCategoryOpen(db.name,itemSchema.name,category.key)?'lucide:chevron-down':'lucide:chevron-right'" /></span><Icon :icon="category.icon" class="node-icon" /><span class="node-label">{{ category.label }}</span><span class="node-count">{{ filtered(contextObjects(db.name,itemSchema.name)[category.key]).length }}</span>
                </button>
                <template v-if="isCategoryOpen(db.name,itemSchema.name,category.key)">
                  <div v-for="item in filtered(contextObjects(db.name,itemSchema.name)[category.key])" :key="`${category.key}:${item.object_id ?? item.name}`" class="tree-node object-node schema-object-node" role="treeitem" tabindex="0" @click="emit('inspect',item,db.name,itemSchema.name)" @keydown.enter="category.key==='tables'?emit('openTable',item.name,db.name,itemSchema.name):emit('openObject',category.key,item,db.name,itemSchema.name)" @dblclick="category.key==='tables'?emit('openTable',item.name,db.name,itemSchema.name):emit('openObject',category.key,item,db.name,itemSchema.name)" @contextmenu.prevent="category.key==='tables'?emit('tableMenu',$event,item,db.name,itemSchema.name):emit('objectMenu',$event,category.key,item,db.name,itemSchema.name)">
                    <span class="tree-indent level-four" /><span class="twisty placeholder" /><Icon :icon="iconFor(category.key)" class="node-icon" /><span class="node-label">{{ item.name }}{{ item.identity_arguments !== undefined ? `(${item.identity_arguments})` : '' }}</span><button v-if="category.key==='tables'&&!isMssql" class="row-action" title="设计表" @click.stop="emit('designTable',item.name,db.name,itemSchema.name)"><Icon icon="lucide:panel-top-open" /></button>
                  </div>
                </template>
              </template>
            </template>
          </template>
        </template>
        <template v-else>
          <div v-if="!contextObjects(db.name)" class="tree-loading">正在读取对象…</div>
          <template v-for="category in definitions" v-else :key="category.key">
            <button class="tree-node category-node" role="treeitem" :aria-expanded="isCategoryOpen(db.name,'',category.key)" @click="toggleCategory(db.name,'',category.key)">
              <span class="tree-indent level-two" /><span class="twisty"><Icon :icon="isCategoryOpen(db.name,'',category.key)?'lucide:chevron-down':'lucide:chevron-right'" /></span><Icon :icon="category.icon" class="node-icon" /><span class="node-label">{{ category.label }}</span><span class="node-count">{{ filtered(contextObjects(db.name)[category.key]).length }}</span>
            </button>
            <template v-if="isCategoryOpen(db.name,'',category.key)">
              <div v-for="item in filtered(contextObjects(db.name)[category.key])" :key="`${category.key}:${item.object_id ?? item.name}`" class="tree-node object-node" role="treeitem" tabindex="0" @click="emit('inspect',item,db.name,'')" @keydown.enter="category.key==='tables'?emit('openTable',item.name,db.name,''):emit('openObject',category.key,item,db.name,'')" @dblclick="category.key==='tables'?emit('openTable',item.name,db.name,''):emit('openObject',category.key,item,db.name,'')" @contextmenu.prevent="category.key==='tables'?emit('tableMenu',$event,item,db.name,''):emit('objectMenu',$event,category.key,item,db.name,'')">
                <span class="tree-indent level-three" /><span class="twisty placeholder" /><Icon :icon="iconFor(category.key)" class="node-icon" /><span class="node-label">{{ item.name }}{{ item.identity_arguments !== undefined ? `(${item.identity_arguments})` : '' }}</span><button v-if="category.key==='tables'" class="row-action" title="设计表" @click.stop="emit('designTable',item.name,db.name,'')"><Icon icon="lucide:panel-top-open" /></button>
              </div>
            </template>
          </template>
        </template>
      </template>
    </template>
  </div>
</template>
