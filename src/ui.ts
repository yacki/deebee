import type { App } from 'vue';
import ArcoVue, { useLocale } from '@arco-design/web-vue';
import WorkbenchInput from './components/ui/WorkbenchInput.vue';
import WorkbenchSelect from './components/ui/WorkbenchSelect.vue';
import WorkbenchCheckbox from './components/ui/WorkbenchCheckbox.vue';
import FilePicker from './components/ui/FilePicker.vue';
import WorkbenchModal from './components/ui/WorkbenchModal.vue';
import WorkbenchTabs from './components/ui/WorkbenchTabs.vue';
import IconButton from './components/ui/IconButton.vue';
import './arco-theme.less';
import './ui.css';

export function installUi(app: App) {
  useLocale('zh-CN');
  app.use(ArcoVue);
  app.component('WorkbenchInput', WorkbenchInput);
  app.component('WorkbenchSelect', WorkbenchSelect);
  app.component('WorkbenchCheckbox', WorkbenchCheckbox);
  app.component('FilePicker', FilePicker);
  app.component('IconButton', IconButton);
  app.component('WorkbenchTabs',WorkbenchTabs);
  app.component('WorkbenchModal',WorkbenchModal);
  return app;
}
