<script setup>
const props = defineProps({
  workspaces: Array,
  sessions: Array,
  activeSession: String,
});
const emit = defineEmits(['select-session', 'new-session', 'add-workspace', 'delete-session']);

function sessionsFor(wsPath) {
  return props.sessions.filter((s) => s.workspace === wsPath);
}
</script>

<template>
  <aside class="sidebar">
    <header>
      <div class="brand">Coding Agent Workspace</div>
      <button class="add-ws" @click="emit('add-workspace')">＋ 添加工作区</button>
    </header>
    <div class="list">
      <div v-for="ws in workspaces" :key="ws.path" class="ws-group">
        <div class="ws-head" :title="ws.path">
          <span class="ws-name">📁 {{ ws.name }}</span>
          <button class="ws-new" title="在该工作区新建会话" @click="emit('new-session', ws.path)">＋</button>
        </div>
        <div v-if="!sessionsFor(ws.path).length" class="empty">暂无会话</div>
        <div
          v-for="s in sessionsFor(ws.path)"
          :key="s.id"
          class="side-item"
          :class="{ active: s.id === activeSession }"
          @click="emit('select-session', s.id)"
        >
          <span class="del" title="删除" @click.stop="emit('delete-session', s.id)">×</span>
          <div class="t">{{ s.title || '(新会话)' }}</div>
          <div class="m"><span class="status-dot" :class="s.status"></span>{{ s.status }} · {{ s.message_count }} 条</div>
        </div>
      </div>
      <div v-if="!workspaces.length" class="empty">暂无工作区</div>
    </div>
  </aside>
</template>
