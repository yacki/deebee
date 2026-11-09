import { h, ref } from 'vue';
import { Input, Modal } from '@arco-design/web-vue';

let dialogId=0;
function labelDialog(className:string,title:string){const element=document.querySelector('.'+className);element?.setAttribute('role','dialog');element?.setAttribute('aria-modal','true');element?.setAttribute('aria-label',title);element?.querySelector('[aria-label=Close]')?.setAttribute('aria-label','关闭');}

export function confirmAction(content: string): Promise<boolean> {
  const className='workbench-confirm-'+(++dialogId);
  return new Promise(resolve => Modal.confirm({modalClass:className,onOpen:()=>labelDialog(className,'操作确认'), title: '操作确认', content, okText: '确认', cancelText: '取消', maskClosable: false, onOk: () => resolve(true), onCancel: () => resolve(false), onClose: () => resolve(false) }));
}
export function promptValue(title: string, initial = ''): Promise<string | null> {
  return new Promise(resolve => {
    const value = ref(initial);
    const className='workbench-prompt-'+(++dialogId);
    const dialog = Modal.open({ title,modalClass:className,onOpen:()=>labelDialog(className,title), okText: '确认', cancelText: '取消', maskClosable: false,
      content: () => h(Input, { modelValue: value.value, inputAttrs: { 'aria-label': title, autofocus: true }, 'onUpdate:modelValue': (next: string) => { value.value = next; }, onPressEnter: () => { resolve(value.value); dialog.close(); } }),
      onOk: () => resolve(value.value), onCancel: () => resolve(null), onClose: () => resolve(null),
    });
  });
}
