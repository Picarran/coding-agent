<script setup>
import { ref, watch } from 'vue';
import { api } from '../api';

const props = defineProps({ sessionId: String });
const data = ref(null);

function load() {
  if (!props.sessionId) { data.value = null; return; }
  api('/api/sessions/' + props.sessionId + '/metrics')
    .then((d) => { data.value = d; })
    .catch(() => { data.value = null; });
}
watch(() => props.sessionId, load, { immediate: true });
</script>

<template>
  <div class="metrics">
    <div v-if="!data" class="empty">暂无指标（运行任务后显示）。</div>
    <template v-else>
      <div class="m-cards">
        <div class="m-card"><div class="k">总 tokens</div><div class="v">{{ data.aggregate.total_tokens }}</div></div>
        <div class="m-card"><div class="k">输入 / 输出</div><div class="v">{{ data.aggregate.prompt_tokens }} / {{ data.aggregate.completion_tokens }}</div></div>
        <div class="m-card"><div class="k">LLM 调用</div><div class="v">{{ data.aggregate.llm_calls }}</div></div>
        <div class="m-card"><div class="k">工具调用</div><div class="v">{{ data.aggregate.tool_calls }}</div></div>
        <div class="m-card"><div class="k">耗时</div><div class="v">{{ data.aggregate.duration_ms != null ? data.aggregate.duration_ms + 'ms' : '—' }}</div></div>
      </div>

      <h4 class="m-title">每次任务</h4>
      <table class="m-table">
        <thead>
          <tr><th>任务</th><th>输入</th><th>输出</th><th>LLM</th><th>工具</th><th>耗时</th></tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in data.tasks" :key="i">
            <td class="tlabel">{{ t.label }}</td>
            <td>{{ t.prompt_tokens }}</td>
            <td>{{ t.completion_tokens }}</td>
            <td>{{ t.llm_calls }}</td>
            <td>{{ t.tool_calls }}</td>
            <td>{{ t.duration_ms || '—' }}</td>
          </tr>
          <tr v-if="!data.tasks.length"><td colspan="6" class="empty">暂无</td></tr>
        </tbody>
      </table>
    </template>
  </div>
</template>
