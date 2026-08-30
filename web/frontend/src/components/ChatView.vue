<script setup>
import StepCard from './StepCard.vue';
import { agentLabel } from '../agentLabel';

defineProps({ turns: Array, streaming: Boolean });

function agentsOf(turn) {
  return (turn.agents || []).map(agentLabel).filter(Boolean).join(', ');
}
</script>

<template>
  <div class="chat">
    <div v-if="!turns.length" class="empty">在下方输入任务，与 agent 对话。</div>
    <div v-for="t in turns" :key="t.id" class="turn-block">
      <div class="bubble-row user"><div class="bubble">{{ t.userText }}</div></div>

      <details v-if="t.stepOrder.length" class="process" open>
        <summary>
          <span class="status-dot" :class="t.status"></span>
          {{ t.stepOrder.length }} 步处理过程
          <span v-if="agentsOf(t)" class="agents">（{{ agentsOf(t) }}）</span>
        </summary>
        <div class="body">
          <StepCard v-for="sid in t.stepOrder" :key="sid" :step="t.steps[sid]" />
        </div>
      </details>

      <div class="bubble-row assistant">
        <div class="bubble">
          <div class="who">{{ agentLabel(t.answerAgent) || 'agent' }}</div>
          {{ t.assistantText || (t.status === 'running' ? '…' : '') }}
        </div>
      </div>
    </div>
  </div>
</template>
