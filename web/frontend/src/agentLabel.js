// Friendly names + colors for agent ids (multi-agent visibility).
const LABELS = {
  main: 'Main',
  explorer: 'Explorer',
  coding: 'Coding',
  test: 'Test',
  single: 'Agent',
  task_router: 'Router',
  side_quest: 'Side',
};

export function agentLabel(id) {
  if (!id) return '';
  const base = String(id).replace(/_agent$/, '');
  return LABELS[base] || id;
}

export const AGENT_COLORS = {
  Main: '#8250df',
  Explorer: '#0969da',
  Coding: '#1a7f37',
  Test: '#bf8700',
  Agent: '#0969da',
  Router: '#656d76',
  Side: '#656d76',
};
