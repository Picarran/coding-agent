<script setup>
defineProps({ desc: String });
const emit = defineEmits(['action']);
</script>

<template>
  <div class="approval">
    <div class="desc">需要审批：{{ desc }}</div>
    <div class="actions">
      <button class="allow" @click="emit('action', 'allow')">允许一次</button>
      <button class="allow" @click="emit('action', 'always')">始终允许</button>
      <button class="deny" @click="emit('action', 'deny')">拒绝</button>
    </div>
  </div>
</template>
