<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, ref } from "vue";
import type { DbObject, Objects, ObjectsTab } from "../types";
const props = defineProps<{ tab: ObjectsTab; objects?: Objects }>();
const emit = defineEmits<{ openTable: [name: string]; designTable: [name: string]; menu: [event: MouseEvent, item: DbObject] }>();
const filter = ref("");
const items = computed(() => (props.objects?.[props.tab.kind] || []).filter(item => item.name.toLowerCase().includes(filter.value.toLowerCase())));
function bytes(value?: number) { if (value == null) return "—"; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; }
</script>
<template>
  <section class="object-list-view">
    <header class="view-header"><div><Icon icon="lucide:folder-tree" /><span><strong>{{ tab.title }}</strong><small>{{ tab.database }}{{ tab.schema ? ` · ${tab.schema}` : '' }}</small></span></div><label><Icon icon="lucide:search" /><input v-model="filter" aria-label="筛选对象" placeholder="筛选对象" /></label></header>
    <div class="object-table">
      <table>
        <thead><tr><th>名称</th><th>行数</th><th>数据大小</th><th>索引大小</th><th>引擎</th><th>创建时间</th><th>更新时间</th><th>排序规则</th><th>注释</th></tr></thead><tbody>
          <tr v-for="item in items" :key="item.name" @dblclick="tab.kind === 'tables' ? emit('openTable', item.name) : undefined" @contextmenu.prevent="emit('menu', $event, item)"><td><Icon :icon="tab.kind === 'tables' ? 'lucide:table-2' : 'lucide:box'" />{{ item.name }}</td><td>{{ item.estimated_rows ?? '—' }}</td><td>{{ bytes(item.data_length) }}</td><td>{{ bytes(item.index_length) }}</td><td>{{ item.engine || '—' }}</td><td>{{ item.created_at || '—' }}</td><td>{{ item.updated_at || '—' }}</td><td>{{ item.collation || '—' }}</td><td>{{ item.comment || '—' }}</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
