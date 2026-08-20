<script setup lang="ts">
import { Icon } from "@iconify/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { MenuItem, MenuState } from "../types";

const props = defineProps<{ menu: MenuState | null }>();
const emit = defineEmits<{ close: [] }>();
const root = ref<HTMLElement>();
const active = ref(0);
const submenu = ref<string>();
const position = computed(() => ({ left: `${Math.min(props.menu?.x || 0, innerWidth - 306)}px`, top: `${Math.min(props.menu?.y || 0, innerHeight - 420)}px` }));
const enabledItems = computed(() => props.menu?.items.filter(item => !item.separator && !item.disabled) || []);

watch(() => props.menu, async () => { active.value = 0; submenu.value = undefined; await nextTick(); root.value?.focus(); });
function closeOutside(event: PointerEvent) { if (props.menu && !root.value?.contains(event.target as Node)) emit("close"); }
function key(event: KeyboardEvent) {
  if (!props.menu) return;
  if (event.key === "Escape") return emit("close");
  if (event.key === "ArrowDown") { event.preventDefault(); active.value = (active.value + 1) % Math.max(1, enabledItems.value.length); }
  if (event.key === "ArrowUp") { event.preventDefault(); active.value = (active.value - 1 + enabledItems.value.length) % Math.max(1, enabledItems.value.length); }
  if (event.key === "Enter") void activate(enabledItems.value[active.value]);
}
async function activate(item?: MenuItem) {
  if (!item || item.disabled) return;
  if (item.children?.length) { submenu.value = submenu.value === item.id ? undefined : item.id; return; }
  emit("close"); await item.action?.();
}
onMounted(() => document.addEventListener("pointerdown", closeOutside, true));
onBeforeUnmount(() => document.removeEventListener("pointerdown", closeOutside, true));
</script>

<template>
  <div v-if="menu" ref="root" class="context-menu" :style="position" role="menu" :aria-label="menu.title" tabindex="-1" @keydown="key">
    <template v-for="item in menu.items" :key="item.id">
      <div v-if="item.separator" class="menu-separator" />
      <div v-else class="menu-slot" @mouseenter="item.children && (submenu = item.id)">
        <button role="menuitem" :class="{ active: enabledItems[active]?.id === item.id, danger: item.danger }" :disabled="item.disabled" @click="activate(item)">
          <Icon :icon="item.icon || 'lucide:circle'" class="menu-icon" />
          <span>{{ item.label }}</span><kbd>{{ item.shortcut }}</kbd><Icon v-if="item.children" icon="lucide:chevron-right" />
        </button>
        <div v-if="item.children && submenu === item.id" class="context-submenu" role="menu">
          <button v-for="child in item.children" :key="child.id" role="menuitem" :class="{ danger: child.danger }" @click="activate(child)">
            <Icon :icon="child.icon || 'lucide:circle'" class="menu-icon" /><span>{{ child.label }}</span><kbd>{{ child.shortcut }}</kbd>
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
