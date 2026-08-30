<script setup>
import ToolCall from './ToolCall.vue';
defineProps({ step: Object });
</script>

<template>
  <details class="step-card" open>
    <summary>
      <span class="badge" :class="step.status">{{ step.status }}</span>
      <span class="desc">{{ step.id }} · {{ step.description }}</span>
    </summary>
    <div class="body">
      <ToolCall v-for="t in step.tools" :key="t.id" :tool="t" />
      <div v-if="!step.tools.length" class="empty">无工具调用</div>
    </div>
  </details>
</template>
