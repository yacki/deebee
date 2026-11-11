<script setup lang="ts">
import { Icon } from '@iconify/vue';
import { computed, onBeforeUnmount, onMounted, watch, nextTick, ref } from 'vue';
import type { MenuItem, MenuState } from '../types';
const props=defineProps<{menu:MenuState|null}>();
const emit=defineEmits<{close:[]}>();
const anchor=ref<HTMLElement>();
const popupHeight=ref(Math.max(120,window.innerHeight-24));
const viewportWidth=ref(window.innerWidth);
const anchorPosition=computed(()=>{
  const height=Math.min((props.menu?.items||[]).reduce((sum,item)=>sum+(item.separator?9:36),0),popupHeight.value)+10;
  return {left:`${Math.max(8,Math.min(props.menu?.x||0,viewportWidth.value-288))}px`,top:`${Math.max(8,Math.min(props.menu?.y||0,window.innerHeight-height-8))}px`};
});
function resize(){popupHeight.value=Math.max(120,window.innerHeight-24);viewportWidth.value=window.innerWidth;}
async function activate(item:MenuItem){if(item.disabled)return;emit('close');await item.action?.();}
function escape(event:KeyboardEvent){if(event.key==='Escape')emit('close');}
watch(()=>props.menu,()=>void nextTick(()=>anchor.value?.focus()));
window.addEventListener('keydown',escape);
onMounted(()=>window.addEventListener('resize',resize));
onBeforeUnmount(()=>{window.removeEventListener('keydown',escape);window.removeEventListener('resize',resize);});
</script>
<template>
  <a-dropdown v-if="menu" :key="`${menu.x}:${menu.y}`" :popup-visible="true" :popup-max-height="popupHeight" popup-class="workbench-context-popup" animation-name="" trigger="click" position="bl" @popup-visible-change="visible=>{if(!visible)emit('close')}">
    <span ref="anchor" tabindex="-1" class="context-menu-anchor" :style="anchorPosition" />
    <template #content>
      <div class="workbench-context-menu" role="menu" :aria-label="menu.title">
        <template v-for="item in menu.items" :key="item.id">
          <a-divider v-if="item.separator" :margin="4" />
          <a-dsubmenu v-else-if="item.children" trigger="hover" popup-class="workbench-context-popup" animation-name="" :option-props="{role:'menuitem','aria-label':item.label,tabindex:0,'aria-haspopup':'menu','aria-disabled':!!item.disabled}" :disabled="item.disabled" :value="item.id"><template #default><Icon :icon="item.icon||'lucide:folder'" /> {{ item.label }}</template><template #content><a-doption v-for="child in item.children" :key="child.id" role="menuitem" tabindex="0" :disabled="child.disabled" @keydown.enter.prevent="activate(child)" @keydown.space.prevent="activate(child)" @click="activate(child)"><Icon :icon="child.icon||'lucide:circle'" /> {{ child.label }}</a-doption></template></a-dsubmenu>
          <a-doption v-else role="menuitem" :aria-label="item.label" tabindex="0" :disabled="item.disabled" :class="{'danger-action':item.danger}" @keydown.enter.prevent="activate(item)" @keydown.space.prevent="activate(item)" @click="activate(item)"><Icon :icon="item.icon||'lucide:circle'" /><span>{{ item.label }}</span><kbd v-if="item.shortcut">{{ item.shortcut }}</kbd></a-doption>
        </template>
      </div>
    </template>
  </a-dropdown>
</template>
<style scoped>
.context-menu-anchor{position:fixed;width:0;height:0;pointer-events:none;outline:none}.workbench-context-menu{width:278px}.workbench-context-menu :deep(.arco-dropdown-option-content){display:flex;align-items:center;gap:9px}.workbench-context-menu :deep(svg){width:16px;height:16px;flex:none}.workbench-context-menu kbd{margin-left:auto;color:var(--color-text-3);font:var(--deebee-font-size) ui-monospace,monospace}.workbench-context-menu :deep(.danger-action){color:rgb(var(--danger-6))}
:global(.workbench-context-popup .arco-dropdown-option-content){display:flex;align-items:center;gap:9px}:global(.workbench-context-popup .arco-dropdown-option-content>svg){width:16px;height:16px;flex:none}
</style>
