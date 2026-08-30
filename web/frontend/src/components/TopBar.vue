<script setup>
defineProps({ title: String, status: String, view: String });
const emit = defineEmits(['view']);
</script>

<template>
  <div class="topbar">
    <div class="title">{{ title }}</div>
    <span v-if="status" class="status-dot" :class="status"></span>
    <div class="tabs">
      <button :class="{ active: view === 'chat' }" @click="emit('view', 'chat')">对话</button>
      <button :class="{ active: view === 'trace' }" @click="emit('view', 'trace')">轨迹</button>
    </div>
  </div>
</template>
