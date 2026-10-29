<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { aiRequest, type AISettings } from '../ai';

const config = ref<AISettings>({ enabled: false, share_schema: false, base_url: '', model: '', has_api_key: false, timeout_seconds: 10 });
const apiKey = ref(''), clearKey = ref(false), busy = ref(false), ready = ref(false), error = ref(''), message = ref('');
async function refresh() {
  busy.value = true; error.value = '';
  try { config.value = await aiRequest('/admin/v1/ai/settings'); apiKey.value = ''; clearKey.value = false; ready.value = true; }
  catch (e) { error.value = e instanceof Error ? e.message : '读取 AI 配置失败'; }
  finally { busy.value = false; }
}
async function submit(test = false) {
  busy.value = true; error.value = ''; message.value = '';
  const { enabled, share_schema, base_url, model, timeout_seconds } = config.value;
  const body = { enabled, share_schema, base_url, model, timeout_seconds, api_key: apiKey.value, clear_api_key: clearKey.value };
  try {
    if (test) {
      const result = await aiRequest<{ message: string }>('/admin/v1/ai/test', { body });
      message.value = result.message;
    } else {
      config.value = await aiRequest('/admin/v1/ai/settings', { method: 'PUT', body });
      apiKey.value = ''; clearKey.value = false; message.value = 'AI 配置已保存。工作台在切回编辑器或刷新后生效。';
    }
  } catch (e) { error.value = e instanceof Error ? e.message : '操作失败'; }
  finally { busy.value = false; }
}
onMounted(refresh);
defineExpose({ refresh });
</script>

<template>
  <section class="access-panel ai-settings">
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <a-alert v-if="message" type="success">{{ message }}</a-alert>
    <a-form :model="config" layout="vertical" @submit-success="submit()">
      <fieldset :disabled="busy || !ready">
        <h2>让命令接着写下去</h2>
        <p>配置兼容 Chat Completions 的模型服务。支持自然语言生成 SQL／服务器命令，也支持输入时自动续写，按 Tab 接受、Esc 忽略。</p>
        <div class="access-field"><a-checkbox v-model="config.enabled">启用 AI 命令续写</a-checkbox></div>
        <div class="access-field"><span>模型服务地址</span><a-input v-model="config.base_url" :input-attrs="{'aria-label':'模型服务地址'}" placeholder="https://your-model-gateway.example/v1" /><small>填写 API 基础地址，系统会追加 /chat/completions。支持企业内部 HTTPS 模型网关。</small></div>
        <div class="access-field"><span>模型名称</span><a-input v-model="config.model" :input-attrs="{'aria-label':'模型名称'}" placeholder="填写服务商提供的模型名称或接入点 ID" /></div>
        <div class="access-field"><span>API Key</span><a-input v-model="apiKey" type="password" :disabled="clearKey" :input-attrs="{'aria-label':'AI API Key',autocomplete:'new-password'}" :placeholder="config.has_api_key?'已保存密钥，留空保持不变':'如模型网关需要认证，请填写密钥'" /><small>密钥在服务端加密保存，不回传浏览器。更换服务地址时需重新填写密钥。</small></div>
        <a-checkbox v-if="config.has_api_key" v-model="clearKey">清除已保存的密钥</a-checkbox>
        <div class="access-field"><span>响应超时（秒）</span><a-input-number v-model="config.timeout_seconds" :min="2" :max="30" aria-label="AI 响应超时" /></div>
        <div class="access-field"><a-checkbox v-model="config.share_schema">发送当前编辑器已加载的表名与字段名，帮助 AI 理解数据库结构</a-checkbox></div>
        <a-alert type="info">启用后，光标附近的命令文本会发送到上述模型服务。不会读取查询结果或终端输出；数据库结构默认不发送。建议只填入编辑器，执行仍由用户触发。</a-alert>
        <div class="access-actions"><a-button :disabled="busy || !ready" @click="submit(true)">{{ busy?'处理中…':'测试连接' }}</a-button><a-button type="primary" html-type="submit" :disabled="busy || !ready">保存配置</a-button></div>
      </fieldset>
    </a-form>
  </section>
</template>

<style scoped>
.ai-settings{max-width:860px}.ai-settings fieldset{border:0;margin:0;padding:0;min-width:0}.ai-settings .access-field{margin:18px 0}.ai-settings small{line-height:1.6;color:var(--color-text-3)}.ai-settings h2{margin-top:4px}.ai-settings p{line-height:1.7}
</style>
