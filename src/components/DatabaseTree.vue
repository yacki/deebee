<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, reactive } from "vue";
import type { Database, DbObject, Objects, Profile } from "../types";

const props = defineProps<{ profile: Profile; serverExpanded: boolean; databases: Database[]; selected: string; expanded: Set<string>; objects: Record<string, Objects>; filter: string }>();
const emit = defineEmits<{
  toggleServer: []; serverMenu: [event: MouseEvent]; selectDatabase: [database: string]; toggleDatabase: [database: string]; openTable: [table: string, database: string]; designTable: [table: string, database: string]; openList: [kind: keyof Objects, database: string]; openObject: [kind: string, object: DbObject, database: string]; databaseMenu: [event: MouseEvent, database: Database]; tableMenu: [event: MouseEvent, table: DbObject, database: string]; objectMenu: [event: MouseEvent, kind: string, object: DbObject, database: string]; inspect: [object: DbObject, database: string];
}>();

const categoryOpen = reactive<Record<string, boolean>>({});
const visibleDatabases = computed(() => props.databases.filter(db => !props.filter || db.name.toLowerCase().includes(props.filter.toLowerCase()) || props.expanded.has(db.name)));
const definitions = [
  { key: "tables" as const, label: "表", icon: "lucide:table-2" },
  { key: "views" as const, label: "视图", icon: "lucide:panels-top-left" },
  { key: "routines" as const, label: "函数与过程", icon: "lucide:function-square" },
  { key: "triggers" as const, label: "触发器", icon: "lucide:zap" },
  { key: "events" as const, label: "事件", icon: "lucide:clock-3" },
];
function categoryKey(database: string, kind: string) { return `${database}:${kind}`; }
function isCategoryOpen(database: string, kind: string) { const key = categoryKey(database, kind); return categoryOpen[key] !== false; }
function toggleCategory(database: string, kind: keyof Objects) { const key = categoryKey(database, kind); categoryOpen[key] = !isCategoryOpen(database, kind); emit("openList", kind, database); }
function dbClick(db: Database) { emit("selectDatabase", db.name); }
function iconFor(kind: keyof Objects) { return definitions.find(item => item.key === kind)?.icon || "lucide:box"; }
function filtered(items: DbObject[]) { return items.filter(item => !props.filter || item.name.toLowerCase().includes(props.filter.toLowerCase())); }
</script>

<template>
  <div class="tree" role="tree" aria-label="数据库对象树">
    <button class="tree-node server-node" role="treeitem" :aria-expanded="serverExpanded" @click="emit('toggleServer')" @contextmenu.prevent="emit('serverMenu', $event)">
      <span class="tree-indent" />
      <span class="twisty" role="button" :aria-label="serverExpanded ? `收起服务器 ${profile.name}` : `展开服务器 ${profile.name}`"><Icon :icon="serverExpanded ? 'lucide:chevron-down' : 'lucide:chevron-right'" /></span>
      <Icon icon="lucide:server" class="node-icon server-icon" />
      <span class="node-label"><b>{{ profile.name }}</b><small>{{ profile.host }}:{{ profile.port }}</small></span>
      <span class="server-driver">MySQL</span>
    </button>
    <template v-for="db in serverExpanded ? visibleDatabases : []" :key="db.name">
      <button class="tree-node db-node" :class="{ active: selected === db.name }" role="treeitem" :aria-expanded="expanded.has(db.name)" @click="dbClick(db)" @contextmenu.prevent="emit('databaseMenu', $event, db)">
        <span class="tree-indent level-one" />
        <span class="twisty" role="button" :aria-label="expanded.has(db.name) ? `收起 ${db.name}` : `展开 ${db.name}`" @click.stop="emit('toggleDatabase', db.name)"><Icon :icon="expanded.has(db.name) ? 'lucide:chevron-down' : 'lucide:chevron-right'" /></span>
        <Icon icon="lucide:database" class="node-icon database-icon" />
        <span class="node-label">{{ db.name }}</span>
      </button>
      <template v-if="expanded.has(db.name)">
        <div v-if="!objects[db.name]" class="tree-loading">正在读取对象…</div>
        <template v-for="category in definitions" v-else :key="category.key">
          <button class="tree-node category-node" role="treeitem" :aria-expanded="isCategoryOpen(db.name, category.key)" @click="toggleCategory(db.name, category.key)">
            <span class="tree-indent level-two" />
            <span class="twisty"><Icon :icon="isCategoryOpen(db.name, category.key) ? 'lucide:chevron-down' : 'lucide:chevron-right'" /></span>
            <Icon :icon="category.icon" class="node-icon" />
            <span class="node-label">{{ category.label }}</span><span class="node-count">{{ filtered(objects[db.name][category.key]).length }}</span>
          </button>
          <template v-if="isCategoryOpen(db.name, category.key)">
            <div
              v-for="item in filtered(objects[db.name][category.key])" :key="`${category.key}:${item.name}`" class="tree-node object-node" role="treeitem" tabindex="0"
              @click="emit('inspect', item, db.name)"
              @keydown.enter="category.key === 'tables' ? emit('openTable', item.name, db.name) : emit('openObject', category.key, item, db.name)"
              @dblclick="category.key === 'tables' ? emit('openTable', item.name, db.name) : emit('openObject', category.key, item, db.name)"
              @contextmenu.prevent="category.key === 'tables' ? emit('tableMenu', $event, item, db.name) : emit('objectMenu', $event, category.key, item, db.name)"
            >
              <span class="tree-indent level-three" /><span class="twisty placeholder" />
              <Icon :icon="iconFor(category.key)" class="node-icon" />
              <span class="node-label">{{ item.name }}</span>
              <button v-if="category.key === 'tables'" class="row-action" :aria-label="`设计 ${item.name}`" title="设计表" @click.stop="emit('designTable', item.name, db.name)"><Icon icon="lucide:panel-top-open" /></button>
            </div>
          </template>
        </template>
        <button class="tree-node category-node" role="treeitem"><span class="tree-indent level-two" /><span class="twisty"><Icon icon="lucide:chevron-right" /></span><Icon icon="lucide:file-code-2" class="node-icon" /><span class="node-label">查询</span><span class="node-count">0</span></button>
        <button class="tree-node category-node" role="treeitem"><span class="tree-indent level-two" /><span class="twisty"><Icon icon="lucide:chevron-right" /></span><Icon icon="lucide:archive-restore" class="node-icon" /><span class="node-label">备份</span><span class="node-count">0</span></button>
      </template>
    </template>
  </div>
</template>
