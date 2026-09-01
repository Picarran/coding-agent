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
    contextTokens: 0,   // latest prompt_tokens (context window usage)
  });
  let es = null;

  function newTurn(userText) {
    return {
      id: ++seq,
      userText: userText || '',
      assistantText: '',
      status: 'running',
      answerAgent: null,
      agents: [],          // agent ids involved this turn (for a summary)
      startTs: null,
      events: [],          // flat chronological records (network-style trace)
      steps: {},           // grouped steps (conversation process view)
      stepOrder: [],
    };
  }

  function rememberAgent(turn, agentId) {
    if (agentId && !turn.agents.includes(agentId)) turn.agents.push(agentId);
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
    const agent = ev.agent_id || null;
    const ts = ev.timestamp;
    const turn = currentTurn();
    if (turn) rememberAgent(turn, agent);

    switch (ev.event_type) {
      case 'SESSION_START': {
        const t = newTurn(p.task || '');
        t.startTs = ts;
        t.events.push({ kind: 'user', ts, agent: null, text: t.userText });
        state.turns.push(t);
        break;
      }
      case 'ROUTE':
        if (turn) turn.events.push({ kind: 'route', ts, agent, text: 'route: ' + (p.route || '') + (p.task_score != null ? ' (score ' + p.task_score + ')' : '') });
        break;
      case 'STEP_START':
        if (turn) {
          state.currentStepId = p.step_id;
          ensureStep(turn, p.step_id, p.description, p.assigned_agent).status = 'running';
          turn.events.push({ kind: 'step', ts, agent: p.assigned_agent, id: p.step_id, desc: p.description });
        }
        break;
      case 'SUBAGENT_START':
        state.roleStep[roleOf(agent)] = p.step_id;
        break;
      case 'PRE_TOOL_USE': {
        if (!turn) break;
        const stepId = state.roleStep[roleOf(agent)] || state.currentStepId || '_single';
        const step = ensureStep(turn, stepId, stepId === '_single' ? 'single agent' : '步骤 ' + stepId, '');
        const args = p.arguments || {};
        const tool = { kind: 'tool', id: p.tool_call_id || 't' + Math.random().toString(36).slice(2), name: p.tool, args, result: '', error: '', diff: null, agent, ts, duration: null };
        if (p.tool === 'patch_file') tool.diff = { label: 'patch: ' + args.path, oldText: args.old_text, newText: args.new_text };
        else if (p.tool === 'write_file') tool.diff = { label: 'write: ' + args.path, oldText: null, newText: args.content };
        step.tools.push(tool);
        turn.events.push(tool);   // the same object also lives in the flat list
        break;
      }
      case 'POST_TOOL_USE': {
        if (!turn) break;
        const t = findTool(turn, p.tool_call_id);
        if (t) { t.result = p.content || ''; t.duration = ev.duration_ms; }
        break;
      }
      case 'TOOL_ERROR': {
        if (!turn) break;
        const t = findTool(turn, p.tool_call_id);
        if (t) t.error = p.error || '';
        break;
      }
      case 'STREAM_DELTA':
        if ((agent === 'main_agent' || agent === 'single_agent') && state.streaming && turn) {
          turn.assistantText += (p.text || '');
          turn.answerAgent = agent;
        }
        break;
      case 'TURN_END':
        if (turn) {
          const raw = (ev.status || 'done').toLowerCase();
          turn.status = raw === 'success' ? 'done' : raw;
          if (!turn.assistantText.trim()) turn.assistantText = p.summary || '';
          state.streaming = false;
          turn.events.push({ kind: 'assistant', ts, agent: turn.answerAgent, text: turn.assistantText, status: turn.status });
        }
        break;
      case 'APPROVAL_PENDING':
        state.approvalId = p.approval_id;
        state.approvalDesc = p.description;
        if (turn) turn.events.push({ kind: 'approval', ts, agent, text: p.description });
        break;
      case 'APPROVAL_GRANTED':
      case 'APPROVAL_REJECTED':
        state.approvalId = null;
        state.approvalDesc = '';
        break;
      case 'LLM_CALL':
        if (p.prompt_tokens != null) state.contextTokens = p.prompt_tokens;
        break;
      case 'CONTEXT_COMPACT':
        // /compact shrinks history; drop the meter by the chars removed.
        if (p.removed_chars != null) {
          state.contextTokens = Math.max(0, state.contextTokens - Math.floor(p.removed_chars / 4));
        }
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
    state.contextTokens = 0;
  }

  return { state, open, close, reset };
}
