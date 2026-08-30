<script setup>
import { ref, computed } from 'vue';
import Sidebar from './components/Sidebar.vue';
import TopBar from './components/TopBar.vue';
import ChatView from './components/ChatView.vue';
import TraceView from './components/TraceView.vue';
import WorkspaceModal from './components/WorkspaceModal.vue';
import ApprovalBanner from './components/ApprovalBanner.vue';
import { api, apiJson } from './api';
import { useAgentSession } from './composables/useAgentSession';

const { state, open, reset } = useAgentSession();

const workspaces = ref([]);
const currentWorkspace = ref('demo_workspace');
const sessions = ref([]);
const sessionId = ref(null);
const view = ref('chat');
const showModal = ref(false);
const draft = ref('');

const activeSession = computed(() => sessions.value.find((s) => s.id === sessionId.value));
const title = computed(() => (activeSession.value ? activeSession.value.title || '(新会话)' : '选择一个会话，或新建会话'));
const status = computed(() => {
  if (state.streaming) return 'running';
  const t = state.turns[state.turns.length - 1];
  return t && t.status ? t.status : (activeSession.value ? activeSession.value.status : '');
});

function loadWorkspaces() {
  api('/api/workspaces').then((ws) => {
    workspaces.value = ws;
    if (!ws.some((w) => w.path === currentWorkspace.value)) currentWorkspace.value = ws[0] ? ws[0].path : 'demo_workspace';
  }).catch(() => {});
}

function loadSessions() {
  api('/api/sessions').then((list) => { sessions.value = list; }).catch(() => {});
}

function selectWorkspace(path) { currentWorkspace.value = path; loadSessions(); }

function selectSession(id) {
  sessionId.value = id;
  reset();
  api('/api/sessions/' + id).then((s) => {
    if (s.workspace) { currentWorkspace.value = s.workspace; }
    loadSessions();
    open(id);
  }).catch(() => {});
}

function newSession(params) {
  const p = params || {};
  apiJson('/api/sessions', 'POST', {
    workspace: currentWorkspace.value,
    orchestration: p.orchestration || 'auto',
    permission_mode: p.permission || 'default',
    max_steps: p.maxSteps || 20,
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
  state.streaming = true;
  apiJson('/api/sessions/' + sessionId.value + '/messages', 'POST', { content: text })
    .then(() => {})
    .catch((e) => { state.streaming = false; alert('发送失败：' + e.message); });
}

function resolveApproval(action) {
  if (!state.approvalId || !sessionId.value) return;
  const approve = action !== 'deny';
  apiJson('/api/sessions/' + sessionId.value + '/approve/' + state.approvalId, 'POST', { approve, always: action === 'always' })
    .catch(console.error);
}

loadWorkspaces();
loadSessions();
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
      <div class="input-bar">
        <textarea
          v-model="draft"
          :disabled="!sessionId || (state.streaming && !state.approvalId)"
          placeholder="输入任务，Enter 发送（Shift+Enter 换行）"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <button :disabled="!draft.trim() || state.streaming || !sessionId" @click="send">发送</button>
      </div>
    </main>
    <WorkspaceModal v-if="showModal" @close="showModal = false" @select="setWorkspace" />
    <ApprovalBanner v-if="state.approvalId" :desc="state.approvalDesc" @action="resolveApproval" />
  </div>
</template>
