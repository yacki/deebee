import type WorkbenchInput from './components/ui/WorkbenchInput.vue';
import type WorkbenchSelect from './components/ui/WorkbenchSelect.vue';
import type WorkbenchCheckbox from './components/ui/WorkbenchCheckbox.vue';
import type FilePicker from './components/ui/FilePicker.vue';
import type WorkbenchModal from './components/ui/WorkbenchModal.vue';
import type WorkbenchTabs from './components/ui/WorkbenchTabs.vue';
import type IconButton from './components/ui/IconButton.vue';
declare module 'vue' {
  export interface GlobalComponents {
    WorkbenchInput: typeof WorkbenchInput;
    WorkbenchSelect: typeof WorkbenchSelect;
    WorkbenchCheckbox: typeof WorkbenchCheckbox;
    FilePicker: typeof FilePicker;
    IconButton: typeof IconButton;
    WorkbenchTabs:typeof WorkbenchTabs;
    WorkbenchModal:typeof WorkbenchModal;
  }
}
export {};
