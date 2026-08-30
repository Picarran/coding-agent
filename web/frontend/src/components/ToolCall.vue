<script setup>
import { computed } from 'vue';
const props = defineProps({ tool: Object });
const prettyArgs = computed(() => JSON.stringify(props.tool.args, null, 2));
</script>

<template>
  <details class="tool">
    <summary><span class="cmd">{{ tool.name }}</span></summary>
    <div>
      <div v-if="tool.diff" class="diff-block">
        <div class="diff-path">{{ tool.diff.label }}</div>
        <pre v-if="tool.diff.oldText != null" class="diff-old">- {{ tool.diff.oldText }}</pre>
        <pre v-if="tool.diff.newText != null" class="diff-new">+ {{ tool.diff.newText }}</pre>
      </div>
      <pre v-else-if="Object.keys(tool.args).length">{{ prettyArgs }}</pre>
      <pre v-if="tool.result" class="result">{{ tool.result }}</pre>
      <pre v-if="tool.error" class="err">error: {{ tool.error }}</pre>
    </div>
  </details>
</template>
