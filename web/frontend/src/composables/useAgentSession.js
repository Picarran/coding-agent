import { reactive } from 'vue';

let seq = 0;

function roleOf(agentId) {
  if (!agentId) return 'single';
  return agentId.endsWith('_agent') ? agentId.slice(0, -6) : agentId;
}

export function useAgentSession() {
  const state = reactive({
    turns: [],
    approvalId: null,
    approvalDesc: '',
    streaming: false,
    currentStepId: null,
    roleStep: {},
  });
  let es = null;

  function newTurn(userText) {
    return {
      id: ++seq,
      userText: userText || '',
      assistantText: '',
      status: 'running',
      events: [],
      steps: {},
      stepOrder: [],
    };
  }

  function ensureStep(turn, id, description, agent) {
    if (!turn.steps[id]) {
      turn.steps[id] = { id, description: description || '', agent: agent || '', status: 'pending', tools: [] };
      turn.stepOrder.push(id);
    }
    return turn.steps[id];
  }

  function currentTurn() {
    return state.turns[state.turns.length - 1] || null;
  }

  function findTool(turn, toolId) {
    for (const sid of turn.stepOrder) {
      const t = turn.steps[sid].tools.find((x) => x.id === toolId);
      if (t) return t;
    }
    return null;
  }

  function handle(ev) {
    const p = ev.payload || {};
    const turn = currentTurn();
    switch (ev.event_type) {
      case 'SESSION_START':
        state.turns.push(newTurn(p.task || ''));
        break;
      case 'STEP_START':
        if (turn) {
          state.currentStepId = p.step_id;
          ensureStep(turn, p.step_id, p.description, p.assigned_agent).status = 'running';
        }
        break;
      case 'SUBAGENT_START':
        state.roleStep[roleOf(ev.agent_id)] = p.step_id;
        break;
      case 'PRE_TOOL_USE': {
        if (!turn) break;
        const stepId = state.roleStep[roleOf(ev.agent_id)] || state.currentStepId || '_single';
        const step = ensureStep(turn, stepId, stepId === '_single' ? 'single agent' : '步骤 ' + stepId, '');
        const args = p.arguments || {};
        const tool = { id: p.tool_call_id || 't' + Math.random().toString(36).slice(2), name: p.tool, args, result: '', error: '', diff: null };
        if (p.tool === 'patch_file') tool.diff = { label: 'patch: ' + args.path, oldText: args.old_text, newText: args.new_text };
        else if (p.tool === 'write_file') tool.diff = { label: 'write: ' + args.path, oldText: null, newText: args.content };
        step.tools.push(tool);
        break;
      }
      case 'POST_TOOL_USE': {
        if (!turn) break;
        const t = findTool(turn, p.tool_call_id);
        if (t) t.result = p.content || '';
        break;
      }
      case 'TOOL_ERROR': {
        if (!turn) break;
        const t = findTool(turn, p.tool_call_id);
        if (t) t.error = p.error || '';
        break;
      }
      case 'STREAM_DELTA':
        if ((ev.agent_id === 'main_agent' || ev.agent_id === 'single_agent') && state.streaming && turn) {
          turn.assistantText += (p.text || '');
        }
        break;
      case 'TURN_END':
        if (turn) {
          turn.status = ev.status || 'done';
          if (!turn.assistantText.trim()) turn.assistantText = p.summary || '';
          state.streaming = false;
        }
        break;
      case 'APPROVAL_PENDING':
        state.approvalId = p.approval_id;
        state.approvalDesc = p.description;
        break;
      case 'APPROVAL_GRANTED':
      case 'APPROVAL_REJECTED':
        state.approvalId = null;
        state.approvalDesc = '';
        break;
    }
  }

  function open(sessionId) {
    close();
    es = new EventSource('/api/sessions/' + sessionId + '/events');
    es.onmessage = (e) => {
      try { handle(JSON.parse(e.data)); } catch (err) { /* malformed */ }
    };
  }

  function close() {
    if (es) {
      es.close();
      es = null;
    }
  }

  function reset() {
    close();
    seq = 0;
    state.turns = [];
    state.approvalId = null;
    state.approvalDesc = '';
    state.streaming = false;
    state.currentStepId = null;
    state.roleStep = {};
  }

  return { state, open, close, reset };
}
