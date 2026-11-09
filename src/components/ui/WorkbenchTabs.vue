<script setup lang="ts">
import { Tabs } from '@arco-design/web-vue';
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref, useAttrs } from 'vue';
defineOptions({inheritAttrs:false});
const root=ref<{$el:HTMLElement}>();
const attrs=useAttrs();
const adaptivePosition=ref<number|'auto'>('auto');
const scrollPosition=computed(()=>{const explicit=attrs.scrollPosition??attrs['scroll-position'];return typeof explicit==='number'||explicit==='auto'||explicit==='start'||explicit==='center'||explicit==='end'?explicit:adaptivePosition.value;});
function keepActiveVisible(){
  const viewport=root.value?.$el.querySelector<HTMLElement>('.arco-tabs-nav-tab');
  const active=root.value?.$el.querySelector<HTMLElement>('.arco-tabs-tab-active');
  if(!viewport||!active)return;
  const outer=viewport.getBoundingClientRect(),tab=active.getBoundingClientRect();
  if(tab.left<outer.left-1||tab.right>outer.right+1)adaptivePosition.value=Math.max(0,viewport.clientWidth-active.offsetWidth);
}
function decorate(){
  const element=root.value?.$el;
  element?.querySelector('.arco-tabs-nav')?.setAttribute('role','tablist');
  element?.querySelectorAll<HTMLElement>('.arco-tabs-tab').forEach(tab=>{
    const active=tab.classList.contains('arco-tabs-tab-active');
    tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
    const close=tab.querySelector<HTMLElement>('.arco-tabs-tab-close-btn');
    if(close){close.setAttribute('role','button');close.setAttribute('aria-label',`关闭 ${tab.textContent?.trim()}`);close.tabIndex=0;}
  });
}
function key(event:KeyboardEvent){
  const target=event.target as HTMLElement;
  if(target.closest('.arco-tabs-tab-close-btn')&&['Enter',' '].includes(event.key)){event.preventDefault();target.closest<HTMLElement>('.arco-tabs-tab-close-btn')?.click();return;}
  const tabs=Array.from(root.value?.$el.querySelectorAll<HTMLElement>('.arco-tabs-tab:not(.arco-tabs-tab-disabled)')||[]);
  const current=tabs.findIndex(tab=>tab.contains(target));
  if(current<0)return;
  let next=current;
  if(event.key==='ArrowRight')next=(current+1)%tabs.length;
  else if(event.key==='ArrowLeft')next=(current+tabs.length-1)%tabs.length;
  else if(event.key==='Home')next=0;
  else if(event.key==='End')next=tabs.length-1;
  else if(event.key==='Enter'||event.key===' '){event.preventDefault();tabs[current]?.click();return;}
  else return;
  event.preventDefault();tabs[next]?.focus();tabs[next]?.click();
}
let observer:MutationObserver|undefined;
let resizeObserver:ResizeObserver|undefined;
onMounted(()=>{decorate();resizeObserver=new ResizeObserver(()=>void nextTick(keepActiveVisible));if(root.value?.$el)resizeObserver.observe(root.value.$el);observer=new MutationObserver(decorate);if(root.value?.$el)observer.observe(root.value.$el,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});});
onUpdated(()=>void nextTick(()=>{decorate();keepActiveVisible();}));onBeforeUnmount(()=>{observer?.disconnect();resizeObserver?.disconnect();});
</script>
<template><Tabs ref="root" v-bind="$attrs" :scroll-position="scrollPosition" @keydown="key"><template v-for="(_,name) in $slots" #[name]="slot"><slot :name="name" v-bind="slot||{}" /></template></Tabs></template>
