<script setup>
import { computed, ref } from 'vue';

const props = defineProps({
  workspaces: Array,
  sessions: Array,
  currentWorkspace: String,
  activeSession: String,
});
const emit = defineEmits(['select-workspace', 'select-session', 'new-session', 'add-workspace', 'delete-session']);

const orchestration = ref('auto');
const permission = ref('default');
const maxSteps = ref(20);

const filtered = computed(() =>
  props.sessions.filter((s) => !props.currentWorkspace || s.workspace === props.currentWorkspace)
);

function create() {
  emit('new-session', {
    orchestration: orchestration.value,
    permission: permission.value,
    maxSteps: Number(maxSteps.value) || 20,
  });
}
</script>

<template>
  <aside class="sidebar">
    <header>
      <div class="brand">Coding Agent Workspace</div>
      <select :value="currentWorkspace" @change="emit('select-workspace', $event.target.value)">
        <option v-for="w in workspaces" :key="w.path" :value="w.path">{{ w.name }}</option>
      </select>
      <div class="ws-actions">
        <button class="primary" @click="create">＋ 新建会话</button>
        <button @click="emit('add-workspace')">＋ 添加工作区</button>
      </div>
      <div class="params">
        <div class="param-row">
          <label>编排</label>
          <select v-model="orchestration">
            <option value="auto">auto</option>
            <option value="fast">fast</option>
            <option value="thorough">thorough</option>
          </select>
        </div>
        <div class="param-row">
          <label>权限</label>
          <select v-model="permission">
            <option value="default">default</option>
            <option value="autonomous">autonomous</option>
            <option value="plan">plan</option>
            <option value="safe">safe</option>
          </select>
        </div>
        <div class="param-row">
          <label>步数</label>
          <input v-model.number="maxSteps" type="number" min="1" max="100" />
        </div>
      </div>
    </header>
    <div class="list">
      <div v-if="!filtered.length" class="empty">该目录下暂无会话</div>
      <div
        v-for="s in filtered"
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
  </aside>
</template>
