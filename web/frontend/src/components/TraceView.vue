<script setup>
import { agentLabel, AGENT_COLORS } from '../agentLabel';

defineProps({ turns: Array });

const KIND = {
  user: { label: 'user', color: '#57606a' },
  route: { label: 'route', color: '#57606a' },
  step: { label: 'step', color: '#8250df' },
  tool: { label: 'tool', color: '#0969da' },
  assistant: { label: 'assistant', color: '#1a7f37' },
  approval: { label: 'approval', color: '#bf8700' },
};

function kindLabel(k) { return (KIND[k] || { label: k }).label; }
function kindColor(k) { return (KIND[k] || { color: '#57606a' }).color; }

function fmtTime(turn, ev) {
  if (!turn.startTs || ev.ts == null) return '';
  const ms = Math.round((ev.ts - turn.startTs) * 1000);
  return ms < 1000 ? '+' + ms + 'ms' : '+' + (ms / 1000).toFixed(2) + 's';
}

function summaryText(ev) {
  if (ev.kind === 'tool') {
    const keys = Object.keys(ev.args || {});
    let extra = '';
    if (keys.length) {
      try { extra = ' ' + JSON.stringify(ev.args).slice(0, 60); } catch (e) { /* ignore */ }
    }
    return ev.name + extra;
  }
  return ev.text || ev.desc || '';
}

function statusOf(ev) {
  if (ev.kind === 'tool') {
    if (ev.error) return '✗';
    if (ev.result) return '✓ ' + (ev.duration != null ? Math.round(ev.duration) + 'ms' : '');
    return '…';
  }
  return '';
}

function agentColor(id) {
  return AGENT_COLORS[agentLabel(id)] || '#57606a';
}

function agentsOf(turn) {
  return (turn.agents || []).map(agentLabel).filter(Boolean).join(', ');
}
</script>

<template>
  <div class="trace">
    <div v-if="!turns.length" class="empty">运行后这里按时间展示 user / assistant / tool 事件流。</div>
    <div v-for="t in turns" :key="t.id" class="net-turn">
      <div class="net-head">
        <span class="net-title">▶ {{ t.userText }}</span>
        <span v-if="agentsOf(t)" class="agents">参与者：{{ agentsOf(t) }}</span>
      </div>
      <div class="net-table">
        <details v-for="(ev, i) in t.events" :key="i" class="net-row" :class="'k-' + ev.kind">
          <summary>
            <span class="net-time">{{ fmtTime(t, ev) }}</span>
            <span class="net-kind" :style="{ background: kindColor(ev.kind) }">{{ kindLabel(ev.kind) }}</span>
            <span class="net-agent" :style="{ color: agentColor(ev.agent) }">{{ agentLabel(ev.agent) }}</span>
            <span class="net-summary">{{ summaryText(ev) }}</span>
            <span class="net-status">{{ statusOf(ev) }}</span>
          </summary>
          <div v-if="ev.kind === 'tool'" class="net-detail">
            <div v-if="ev.diff" class="diff-block">
              <div class="diff-path">{{ ev.diff.label }}</div>
              <pre v-if="ev.diff.oldText != null" class="diff-old">- {{ ev.diff.oldText }}</pre>
              <pre v-if="ev.diff.newText != null" class="diff-new">+ {{ ev.diff.newText }}</pre>
            </div>
            <pre v-else-if="Object.keys(ev.args || {}).length">{{ JSON.stringify(ev.args, null, 2) }}</pre>
            <pre v-if="ev.result" class="result">{{ ev.result }}</pre>
            <pre v-if="ev.error" class="err">error: {{ ev.error }}</pre>
          </div>
        </details>
      </div>
    </div>
  </div>
</template>
