<script setup>
import { ref, computed, watch } from 'vue';
import Sidebar from './components/Sidebar.vue';
import TopBar from './components/TopBar.vue';
import ChatView from './components/ChatView.vue';
import TraceView from './components/TraceView.vue';
import WorkspaceModal from './components/WorkspaceModal.vue';
import ApprovalBanner from './components/ApprovalBanner.vue';
import MetricsView from './components/MetricsView.vue';
import { api, apiJson } from './api';
import { useAgentSession } from './composables/useAgentSession';

const { state, open, reset } = useAgentSession();

const CONTEXT_LIMIT = 64000;

const workspaces = ref([]);
const currentWorkspace = ref(localStorage.getItem('coding-agent-workspace') || '');
const sessions = ref([]);
const sessionId = ref(null);
const view = ref('chat');
const showModal = ref(false);
const draft = ref('');

const orchestration = ref('auto');
const permission = ref('default');
const commands = ref({});
const commandHints = ref([]);
const showCommands = ref(false);
const textarea = ref(null);

const activeSession = computed(() => sessions.value.find((s) => s.id === sessionId.value));
const title = computed(() => (activeSession.value ? activeSession.value.title || '(新会话)' : '选择一个会话，或新建会话'));
const status = computed(() => {
  if (state.streaming) return 'running';
  const t = state.turns[state.turns.length - 1];
  return t && t.status ? t.status : (activeSession.value ? activeSession.value.status : '');
});
const ctxPct = computed(() => Math.min(100, Math.round((state.contextTokens / CONTEXT_LIMIT) * 100)));

// Persist the last-selected workspace so a reload doesn't jump back to root.
watch(currentWorkspace, (v) => { if (v) localStorage.setItem('coding-agent-workspace', v); });

function loadWorkspaces() {
  api('/api/workspaces').then((ws) => {
    workspaces.value = ws;
    const cur = currentWorkspace.value;
    if (!cur) {
      currentWorkspace.value = ws[0] ? ws[0].path : 'demo_workspace';
    } else if (!ws.some((w) => w.path === cur)) {
      // Restore a persisted workspace that lives outside the project root.
      workspaces.value = [...ws, { name: cur.split(/[\\/]/).pop() || cur, path: cur }];
    }
  }).catch(() => {});
}

function loadSessions() {
  api('/api/sessions').then((list) => { sessions.value = list; }).catch(() => {});
}

function loadCommands() {
  api('/api/commands').then((c) => { commands.value = c || {}; }).catch(() => {});
}

function selectWorkspace(path) { currentWorkspace.value = path; loadSessions(); }

function selectSession(id) {
  sessionId.value = id;
  reset();
  api('/api/sessions/' + id).then((s) => {
    if (s.workspace) currentWorkspace.value = s.workspace;
    orchestration.value = s.orchestration || 'auto';
    permission.value = s.permission || 'default';
    loadSessions();
    open(id);
  }).catch(() => {});
}

function newSession(params) {
  const p = params || {};
  apiJson('/api/sessions', 'POST', {
    workspace: currentWorkspace.value,
    orchestration: p.orchestration || orchestration.value,
    permission_mode: p.permission || permission.value,
  })
    .then((d) => { loadSessions(); selectSession(d.session_id); })
    .catch((e) => alert('创建会话失败：' + e.message));
}

function deleteSession(id) {
  api('/api/sessions/' + id, { method: 'DELETE' })
    .then(() => {
      if (sessionId.value === id) { sessionId.value = null; reset(); }
      loadSessions();
    }).catch(() => {});
}

function setWorkspace(path) {
  currentWorkspace.value = path;
  if (!workspaces.value.some((w) => w.path === path)) {
    workspaces.value.push({ name: path.split(/[\\/]/).pop() || path, path });
  }
  showModal.value = false;
  loadSessions();
}

function send() {
  const text = draft.value.trim();
  if (!text || state.streaming || !sessionId.value) return;
  draft.value = '';
  showCommands.value = false;
  state.streaming = true;
  apiJson('/api/sessions/' + sessionId.value + '/messages', 'POST', {
    content: text,
    orchestration: orchestration.value,
    permission: permission.value,
  })
    .then(() => {})
    .catch((e) => { state.streaming = false; alert('发送失败：' + e.message); });
}

function stop() {
  if (!sessionId.value || !state.streaming) return;
  api('/api/sessions/' + sessionId.value + '/stop', { method: 'POST' }).catch(console.error);
}

function onSendOrStop() {
  if (state.streaming) stop();
  else send();
}

function resolveApproval(action) {
  if (!state.approvalId || !sessionId.value) return;
  const approve = action !== 'deny';
  apiJson('/api/sessions/' + sessionId.value + '/approve/' + state.approvalId, 'POST', { approve, always: action === 'always' })
    .catch(console.error);
}

function onInput() {
  const v = draft.value;
  if (v.startsWith('/') && !v.includes(' ')) {
    const q = v.slice(1).toLowerCase();
    commandHints.value = Object.keys(commands.value).filter((k) => k.toLowerCase().startsWith('/' + q));
    showCommands.value = commandHints.value.length > 0;
  } else {
    showCommands.value = false;
  }
}

function pickCommand(cmd) {
  draft.value = cmd + ' ';
  showCommands.value = false;
  if (textarea.value) textarea.value.focus();
}

loadWorkspaces();
loadSessions();
loadCommands();
</script>

<template>
  <div class="app">
    <Sidebar
      :workspaces="workspaces"
      :sessions="sessions"
      :current-workspace="currentWorkspace"
      :active-session="sessionId"
      @select-workspace="selectWorkspace"
      @select-session="selectSession"
      @new-session="newSession"
      @add-workspace="showModal = true"
      @delete-session="deleteSession"
    />
    <main class="workspace">
      <TopBar :title="title" :status="status" :view="view" @view="view = $event" />
      <div class="pane" :class="{ active: view === 'chat' }">
        <ChatView :turns="state.turns" :streaming="state.streaming" />
      </div>
      <div class="pane" :class="{ active: view === 'trace' }">
        <TraceView :turns="state.turns" />
      </div>
      <div class="pane" :class="{ active: view === 'metrics' }">
        <MetricsView :session-id="sessionId" :streaming="state.streaming" />
      </div>
      <div class="input-bar">
        <div class="settings">
          <select v-model="permission" class="pill" title="权限模式">
            <option value="default">default</option>
            <option value="autonomous">autonomous</option>
            <option value="plan">plan</option>
            <option value="safe">safe</option>
          </select>
          <select v-model="orchestration" class="pill" title="编排模式">
            <option value="auto">auto</option>
            <option value="fast">fast</option>
            <option value="thorough">thorough</option>
          </select>
        </div>
        <div class="input-wrap">
          <div v-if="showCommands" class="cmd-dropdown">
            <div v-for="c in commandHints" :key="c" class="cmd-item" @mousedown.prevent="pickCommand(c)">
              <b>{{ c }}</b><span>{{ commands[c] }}</span>
            </div>
          </div>
          <textarea
            ref="textarea"
            v-model="draft"
            :disabled="!sessionId"
            placeholder="输入任务；输入 / 查看命令（Enter 发送，Shift+Enter 换行）"
            @input="onInput"
            @keydown.enter.exact.prevent="onSendOrStop"
          ></textarea>
        </div>
        <button class="send-btn" :disabled="!sessionId" :title="state.streaming ? '停止' : '发送'" @click="onSendOrStop">
          {{ state.streaming ? '⏸' : '↑' }}
        </button>
      </div>
      <div v-if="sessionId" class="metrics-strip">
        <div class="ctx" :title="'上下文占用 ' + state.contextTokens + ' / ' + CONTEXT_LIMIT + ' tokens'">
          <div class="ctx-bar"><div class="ctx-fill" :style="{ width: ctxPct + '%' }"></div></div>
          <span class="ctx-label">ctx {{ state.contextTokens }} / {{ CONTEXT_LIMIT }}</span>
        </div>
        <div class="m-items">
          <span>tokens {{ state.metrics.totalTokens }}</span>
          <span>LLM {{ state.metrics.llmCalls }}</span>
          <span>tool {{ state.metrics.toolCalls }}</span>
          <span v-if="state.metrics.durationMs != null">{{ state.metrics.durationMs }}ms</span>
        </div>
      </div>
    </main>
    <WorkspaceModal v-if="showModal" @close="showModal = false" @select="setWorkspace" />
    <ApprovalBanner v-if="state.approvalId" :desc="state.approvalDesc" @action="resolveApproval" />
  </div>
</template>
