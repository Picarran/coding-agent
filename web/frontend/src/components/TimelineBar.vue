<script setup>
import { computed } from 'vue';
const props = defineProps({ turn: Object });

const TOOL_COLORS = {
  list_files: '#0969da', read_file: '#0969da', search_text: '#0969da',
  patch_file: '#8250df', write_file: '#8250df',
  execute_command: '#bf8700', submit_report: '#1a7f37',
};

const segments = computed(() => {
  const tools = [];
  for (const sid of props.turn.stepOrder || []) {
    for (const t of (props.turn.steps[sid].tools || [])) {
      tools.push({ tool: t.name, color: TOOL_COLORS[t.name] || '#656d76', status: t.error ? 'failed' : 'done' });
    }
  }
  if (!tools.length) return [];
  const w = 100 / tools.length;
  return tools.map((t) => ({ ...t, width: w }));
});

const legend = computed(() => {
  const seen = {};
  return segments.value.filter((s) => (seen[s.tool] ? false : (seen[s.tool] = true)));
});
</script>

<template>
  <div class="timeline">
    <div class="bar" title="工具调用序列">
      <div
        v-for="(seg, i) in segments"
        :key="i"
        class="seg"
        :style="{ width: seg.width + '%', background: seg.color }"
        :title="seg.tool"
      ></div>
    </div>
    <div class="legend">
      <span v-for="s in legend" :key="s.tool"><span class="status-dot" :style="{ background: s.color }"></span>{{ s.tool }}</span>
    </div>
  </div>
</template>
