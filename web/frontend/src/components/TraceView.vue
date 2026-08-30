<script setup>
import StepCard from './StepCard.vue';
import TimelineBar from './TimelineBar.vue';
defineProps({ turns: Array });
</script>

<template>
  <div class="trace">
    <div v-if="!turns.length" class="empty">运行后这里显示计划与工具轨迹。</div>
    <div v-for="t in turns" :key="t.id" class="turn-block">
      <div class="turn-title">▶ {{ t.userText }} <span class="status-dot" :class="t.status"></span></div>
      <TimelineBar :turn="t" />
      <StepCard v-for="sid in t.stepOrder" :key="sid" :step="t.steps[sid]" />
    </div>
  </div>
</template>

<style scoped>
.turn-title { font-size: 13px; font-weight: 600; color: var(--text); margin: 14px 0 8px; display: flex; align-items: center; gap: 8px; }
</style>
