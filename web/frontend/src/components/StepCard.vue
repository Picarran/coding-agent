<script setup>
import ToolCall from './ToolCall.vue';
import { agentLabel, AGENT_COLORS } from '../agentLabel';

defineProps({ step: Object });

function color(agent) {
  return AGENT_COLORS[agentLabel(agent)] || '#57606a';
}
</script>

<template>
  <details class="step-card" open>
    <summary>
      <span class="badge" :class="step.status">{{ step.status }}</span>
      <span v-if="step.agent" class="agent-tag" :style="{ background: color(step.agent) }">{{ agentLabel(step.agent) }}</span>
      <span class="desc">{{ step.id }} · {{ step.description }}</span>
    </summary>
    <div class="body">
      <ToolCall v-for="t in step.tools" :key="t.id" :tool="t" />
      <div v-if="!step.tools.length" class="empty">无工具调用</div>
    </div>
  </details>
</template>
