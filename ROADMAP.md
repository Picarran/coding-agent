# 优化路线图（ROADMAP）

> 状态约定：`- [ ]` 未开始 · `🚧` 进行中 · `- [x]` 已完成
> 当前阶段：**V1 — Agent Runtime 强化**
> 基线：`v1.0.0`（五阶段完整实现的可用版本，位于 `master` 分支）

## 总体目标

把项目从"能运行的 Agent Demo"升级为"**可约束、可审计、可观测、可解释的 Agent Runtime**"，
最终服务于面试答辩（能讲清每个设计为什么）与 2 分钟演示视频（有可展示的深度功能）。

对比对象：Claude Code、Codex、DeepSeek Harness 等成熟 Coding Agent 的工程化思想。

---

## V1 — Agent Runtime 强化（先做，直接提质量）

### 1. 权限控制与 Policy Engine

- **状态**：`- [x]`
- **具体做什么**：
  1. 定义 `PermissionMode` 枚举：`PLAN` / `SAFE` / `DEFAULT` / `AUTONOMOUS`，每种模式对应一份「工具白名单 + 默认策略」。
  2. 定义 `PermissionRule`：`{effect ∈ {deny, ask, allow}, tool, command_pattern/path_pattern}`，规则优先级 **DENY > ASK > ALLOW**。
  3. 实现 `RiskScorer`：`risk = 工具风险 + 命令风险 + 路径风险 + 外部副作用风险`，输出 0–5 分（公式必须可一句话解释）。
  4. 在 `ToolExecutor.execute` **之前**插入 `PermissionChecker`：先规则判定 → 再风险评分 → 决定 `auto-allow / ask / deny`。
  5. 危险命令硬 `DENY` 清单（`rm -rf /`、`format`、`shutdown` 等不可撤销操作）。
  6. 交互模式下需审批的操作弹 `input("允许执行 X? [y/N]")`；非交互模式按策略默认拒绝（fail-closed）。
  7. 通过 CLI 参数切换权限模式（如 `--permission safe`）。
- **达到什么效果**：
  - `PLAN` 模式下 `patch_file`/`write_file`/危险命令被**确定性拒绝**（代码层，非 prompt 层）；
  - 执行 `git push`、`pip install`、`rm` 等命令前**弹出确认**，用户可拒绝；
  - 每次权限决策可追溯：日志记录「决策结果 + 命中的规则/风险分」；
  - 单测覆盖：只读模式禁写、危险命令需审批、DENY 优先于 ALLOW、风险分阈值边界。

### 2. 统一 Event Bus / Trace（= Hook + 可观测一次做掉）

- **状态**：`- [x]`
- **具体做什么**：
  1. 把现有 `Tracer` 协议升级为**完整生命周期事件**：`SESSION_START / AGENT_START / PLAN_CREATED / STEP_START / PRE_TOOL_USE / POST_TOOL_USE / TOOL_ERROR / SUBAGENT_START / SUBAGENT_FINISH / CONTEXT_COMPACT / REPLAN_START / REPLAN_FINISH / APPROVAL_REQUIRED / APPROVAL_GRANTED / APPROVAL_REJECTED / AGENT_FINISH / SESSION_END`。
  2. 定义统一 `TraceEvent`：`{timestamp, session_id, agent_id, event_type, payload, duration_ms, status}`。
  3. 实现三个消费者：`ConsoleTracer`（现状）、`JsonlAuditLogger`（审计日志落盘）、`MetricsCollector`（聚合指标）。
  4. 在 `ReactLoop` / `ToolExecutor` / `MainAgent` / `ContextManager` 关键节点 `emit` 事件。
- **达到什么效果**：
  - 一次运行可产出**完整 JSONL 审计日志**，回溯每一步（哪个 agent、调了什么工具、耗时、结果、权限决策）；
  - 可自动汇总指标：LLM 调用次数、总 token、工具调用次数、成功率、平均延迟、replan 次数、SubAgent 数量、总耗时；
  - 为 V3 的 Web 前端与性能 Dashboard 提供统一数据源。

### 3. 上下文工程（Context Engineering）

- **状态**：`- [x]`
- **具体做什么**：
  1. **token 级预算**：`ContextManager` 用 token 估算（`chars/4` 近似，先不引入重型 tokenizer）替代纯 char 计数，按 token 阈值触发裁剪。
  2. **LLM 摘要压缩**：被裁剪的旧 exchange 先由 LLM 压成一句摘要（保留：关键结论、已修改文件、失败尝试、未解决问题）再丢弃，而非硬删。
  3. **Tool Result 压缩**：`execute_command` 等长输出只提取「失败断言 + 关键 traceback + 相关行」，原始输出另存 artifact。
  4. **Tool Cache**：以 `tool_name + 归一化参数 + workspace_version` 为 key 缓存只读工具结果（`list_files`/`read_file`/`search_text`），文件变化后 workspace_version 递增自动失效。
- **达到什么效果**：
  - 长任务不再因上下文无界增长而崩溃，且旧步骤的**关键信息不丢失**；
  - 大工具输出被压缩，模型收到的 observation 更短，**减少无效 token**；
  - 重复只读调用命中缓存，**减少执行次数与 LLM 往返**。

### 4. 评估体系（eval）

- **状态**：`- [x]`
- **具体做什么**：
  1. 建 `eval/` 目录，放 5–10 个**可复现任务**（每个 = 临时 workspace 种子 + 任务描述 + 验收脚本/条件）。
  2. 写 `eval/runner.py`：逐个跑任务 → 判定通过/失败 → 汇总成功率、平均步骤、平均 token、平均耗时。
  3. 输出报告文件，便于对比优化前后的变化。
- **达到什么效果**：
  - 一句话可复现：**「在 N 个任务上通过率 X%，平均步骤 Y，平均 token Z」**；
  - 每次优化后用同一套任务回归，量化改进/回退。

---

## V2 — Agent Intelligence 强化

### 5. 动态委派 + 并行只读 SubAgent

- **状态**：`- [x]`
- **具体做什么**：实现 `DelegationPolicy`（按复杂度/独立性/上下文规模决定 `direct / delegate / parallel delegate`）；只读 SubAgent（Explorer/Test）允许并行，写 SubAgent（Coding）串行执行。
- **达到什么效果**：简单步骤不再无谓创建 SubAgent；相互独立的探索可并行，缩短总耗时、减少无效 LLM 往返。

### 6. Model Routing

- **状态**：`- [x]`
- **具体做什么**：预留 `ModelRouter.route(task_type)` 接口；Planner/复杂 coding 用强模型，Explorer/摘要/测试解析用快模型；当前单模型下接口可通、后续可插多模型。
- **达到什么效果**：复杂步骤不失智，简单步骤更省 token/延迟；架构支持多模型而不改上层。

### 7. Retrieval Memory

- **状态**：`- [x]`
- **具体做什么**：历史 Artifact（报告/结论）按「关键词 + 文件路径 + 时间 + 任务ID」建索引；新任务查询时取 Top-K 相关片段注入上下文（先不引入向量库）。
- **达到什么效果**：跨任务能检索到"之前分析过的结论"，避免重复探索；上下文只注入相关片段而非全部历史。

### 8. Skill / Workflow

- **状态**：`- [x]`
- **具体做什么**：`skills/` 定义 `fix-tests` / `code-review` / `implement-feature` / `refactor` 等工作流模板（工具约束 + 步骤顺序 + 验证策略）；Main Agent 先做 Skill 匹配，命中则按模板执行。
- **达到什么效果**：高频任务走确定性模板，比每次让 Planner 从零规划更稳定、更可预期。

---

## MCP — Model Context Protocol

> 状态：`- [x] 已完成`（编号 V2-9）
> 实现：`src/mcp/{config,client,manager}.py` + `--mcp-config` CLI + `extra_tools` 注入通道；示例 `examples/mcp_servers/`。

### 什么是 MCP

MCP（Model Context Protocol，Anthropic 提出的开放协议）让 AI 应用以**统一方式接入外部工具/数据**。它定义 client（我们的 agent）↔ server（外部能力提供方，一个进程）之间的 JSON-RPC 通信：client 启动 server → `initialize` 握手 → `tools/list` 发现该 server 暴露的工具 → 模型调用时 `tools/call` 转发执行并拿回结果。传输用 **stdio**（本地子进程）或 SSE/HTTP（远程）。

对标 Claude Code：`claude mcp add <name> <command>` 注册一个 server 进程后，它的工具就和内置工具一样出现在模型视野里，无独立的"MCP"概念。

### 能实现什么效果

- **接任意外部能力而不改核心**：GitHub/GitLab（读 issue/PR）、文件系统、数据库、浏览器、Slack…只要有人写了 MCP server，我们的 agent 就能用。
- **工具即插即用**：加一条 server 配置就多一组工具，模型自动可见、可调用，无需改 agent 代码。

### 实现方案（映射到现有架构）

1. **配置** `.coding-agent/mcp.json`（或 CLI `--mcp-config <path>`）：
   ```json
   {"servers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}}}
   ```
2. **`src/mcp/client.py`（最小 stdio 客户端，零新增依赖）**：`subprocess.Popen` 拉起 server → `initialize` → `tools/list` → 暴露 `call(tool, args)`。
3. **注册进现有 `ToolRegistry`**：每个 MCP 工具包装成 `ToolDefinition(name="mcp__<server>__<tool>", parameters=inputSchema, func=client.call)`，自动复用 `ToolExecutor`（参数校验 / 权限 / 事件 / 审计 / 错误归一）。`mcp__` 前缀避免与内置工具撞名。
4. **权限/安全**：MCP 工具 = 任意外部进程，基础风险设成与 `execute_command` 同等（3），default 模式触发审批；文档明示"你加的 server 会执行它想执行的代码"。
5. **传输范围**：先做 stdio（覆盖绝大多数本地 server），SSE/HTTP 留接口。

### 分阶段

1. `- [x]` stdio client + `tools/list` / `tools/call` + 注册进 registry（含审批）。
2. `- [x]` CLI `--mcp-config` + 启动时打印已发现的 MCP 工具。
3. `- [x]` 多 server、超时/崩溃兜底、工具名冲突前缀（`mcp__<server>__<tool>`）。

### 实现要点（V2-9 落地后补充）

- `src/mcp/client.py`：零依赖 stdio JSON-RPC 客户端；后台读线程 + `queue` 实现**跨平台超时**（Windows 上 `select` 对管道无效）；Windows `.cmd/.bat` shim（`npx`/`node`）自动走 shell。
- `src/mcp/config.py`：`<workspace>/.coding-agent/mcp.json` 解析为 `MCPServerConfig`（command/args/env/timeout）。
- `src/mcp/manager.py`：一个 server 起一个进程，`tools/list` 结果包装成 `ToolDefinition`（`mcp__<server>__<tool>`），失败 server **记录后跳过**，不拖垮整个 agent。
- 注入通道：`extra_tools: list[ToolDefinition]` 穿到 `build_agent → build_main_agent/build_single_agent → Explorer/Coding/Test`，复用现有 `ToolExecutor`（校验/权限/事件/审计/错误归一）。
- 权限：`RiskScorer` 对 `mcp__*` 基础风险=3（等同 `execute_command`），default 模式每次调用都触发审批；PLAN 模式（白名单）确定性拒绝。

### 风险/取舍

- 每调一次 MCP 工具 = 一次进程间 JSON-RPC 往返，比内置函数慢。
- MCP server 是用户显式授权的代码执行，硬 DENY 清单管不到其内部，安全边界只能靠「高基础风险 + 默认审批」这一层守卫。

---

## V3 — 产品化

### 9. Web Agent Workspace

- **状态**：`- [x]`
- **具体做什么**：FastAPI + SSE 把 Event Bus 事件实时推到前端（`web/server.py` + `web/broker.py` + `web/static/index.html` 单文件前端，零构建）；渲染 Plan Timeline、可折叠 step（`<details>`）、Agent Trace（含 patch/write 的 diff 视图）、Approval UI（允许一次/始终允许/拒绝）、流式回答；`STREAM_DELTA` 事件做 token 级流式输出；`tool_call_id` 关联每次工具调用的结果/错误。
- **达到什么效果**：一个非聊天式的「Agent Workspace」，实时看计划进度、工具轨迹、代码 diff、流式回答，并对审批作出回应。
- **运行**：`python -m uvicorn web.server:app --port 8001` → 浏览器 `http://127.0.0.1:8001`。
- **CLI 端体验**（同批优化）：`ConsoleTracer` 重写——`--quiet` 只留步骤级进度 + 最终回答、`--no-color` 关 ANSI、彩色状态（成功绿/失败红/进行黄）、工具结果默认截断 4000 字符防刷屏。

### 10. 会话持久化 + Session 管理

- **状态**：`- [x]`
- **具体做什么**：按 workspace 保存会话到 `.coding-agent/session.json`（`src/session/store.py`：`load_session`/`save_session`/`default_session_path`）；启动加载（resume）、每轮结束 + 退出时保存；`MainAgentSession` 增 `set_history`/`set_last_plan`/`/session`/`/new` 命令；不同项目目录各自独立会话。
- **达到什么效果**：跨进程/跨天续聊；每个项目目录有独立、可恢复的会话历史。
- **取舍（诚实说明）**：这是「会话续聊」而非「续跑到一半的 plan」——持久化的是滚动历史 + 上次 plan 快照；`MainAgent.run()` 每次从零重规划（不序列化半路 TaskPlan/WorkspaceContext/子任务 artifacts，那需要给整个运行时加可暂停/恢复的序列化边界）。对「跨天续聊」目标已足够。

### 11. 性能 Dashboard

- **状态**：`- [x]`
- **具体做什么**：`MetricsCollector` 增 prompt/completion 分词 + `snapshot`/`reset`；新增 `SessionMetrics`（会话聚合 + 每次任务快照）；CLI 结束打印「会话聚合 + 每任务表（calls/tok_in/tok_out/ms）」；Web 每会话挂 `SessionMetrics` + `GET /api/sessions/{id}/metrics`（冷会话从持久化事件重算）+ 前端「指标」页（聚合卡片 + 每任务表）。（成本计算与展示后续按需求移除。）
- **达到什么效果**：一眼看清 agent 的资源消耗（token/耗时）与效率，支撑"性能优化"的量化论证。

---

## 建议执行顺序

```
V1-1 权限控制 → V1-2 Event/Trace → V1-3 上下文工程（穿插 V1-4 eval）→ V2 → V3
```

理由：V1 三项都直接落在现有架构的"执行点"上（`ToolExecutor` / `Tracer` / `ContextManager`），
改动收敛、不推倒重来，且每一项都同时命中「面试深度 + 视频可演示」。

## 进度日志

| 日期 | 完成项 | 说明 |
|---|---|---|
| 2026-08-28 | — | 路线图建立，尚未开始 V1 |
| 2026-08-28 | V1-1 权限控制与 Policy Engine | 新增 `src/safety/permissions.py`（PermissionMode/Rule/RiskScorer/Checker + 硬 DENY 清单 + fail-closed approver），在 `ToolExecutor.execute` 前插入检查，CLI 增加 `--permission`；24 个新单测，全套 83 测试通过 |
| 2026-08-28 | V1-1 语义强化 | ①「拒绝 = 一等终态信号」：`ToolResult.permission_denied` + `AgentState/MainAgentState.BLOCKED`，拒绝即中断循环并把控制权交还用户（不再喂回模型让其换招绕过）；② 补充 Windows `del/erase/rd/rmdir` 风险覆盖。全套 94 测试通过 |
| 2026-08-28 | V1-1 模式顺序修正 | 把 `execute_command` 的基础风险从 1 提到 3，使**任何命令**在 `plan/safe/default` 三档都触发审批、仅 `autonomous` 放行；删除冗余的 blanket ASK 规则，默认策略统一由「白名单 + 风险阈值」表达。权限严格性单调：`plan(禁写+命令问) > safe(敏感写+命令问) > default(全写自动+命令问) > autonomous(全自动)` |
| 2026-08-28 | V1-2 统一 Event Bus / Trace | 重写 `core/events.py`：`TraceEvent{timestamp,session_id,agent_id,event_type,payload,duration_ms,status}` + `EventBus` + 三消费者（`ConsoleTracer`/`JsonlAuditLogger`/`MetricsCollector`）；在 `ReactLoop`/`ToolExecutor`/`MainAgent`/`ContextManager` 关键节点 emit（含 LLM_CALL 记 token/延迟）；CLI 增加 `--audit-log`，结束打印指标汇总。全套 101 测试通过 |
| 2026-08-28 | V1-4 评估体系（先于 V1-3，用于前后对比） | 建 `eval/`（`tasks.py` 5 个可复现任务：seed+任务+确定性 verify 判定；`runner.py` 临时目录播种→autonomous 跑 MainAgent→MetricsCollector 收指标→判定→汇总→JSON 报告）；`--dry-run` 脚本化 mock 离线验证整条链路。全套 112 测试通过。**待办：跑真实 baseline**（`python -m eval.runner`，需 DEEPSEEK_API_KEY）作为 V1-3 前后对比基准 |
| 2026-08-28 | eval 前端展示平台（V1-4 延伸） | FastAPI `eval/server.py` + `eval/store.py`（RunStore 持久化 `eval/runs/*.json`）+ 单页前端 `eval/web/index.html`：手工勾选任务/参数发起运行、轮询展示结果、历史记录、**多 run 对比（表格 + CSS 柱状图）**。已实测 API + dry-run 全链路。运行：`python -m uvicorn eval.server:app --port 8000` |
| 2026-08-28 | eval 支持 multi vs single 对比 | `eval/runner.py` 新增 `build_single_agent`（单 ReAct 循环 + 全工具集，无 Planner/SubAgent）+ `--agent {multi,single}`；`server.py`/前端增加 Agent 下拉与 `agent_mode` 字段，可**同一套任务对比「多 agent(MainAgent) vs 单 agent(ReAct)」的成功率/token/耗时**。已实测 API 单 agent dry-run |
| 2026-08-28 | eval 加复杂任务 + V1-3 指标 | 新增 2 个**多步/跨文件**复杂任务 `split_module`（函数拆到新模块+更新 3 处 import）与 `fix_data_flow`（跨 data→stats→report 数据流修 2 处 bug），标记 `complex`；新增 V1-3 面向指标 `tokens_per_tool_call`（工具结果压缩见效）与 `context_compactions`（上下文裁剪计数），前端结果卡/对比表/逐任务表展示。全套 118 测试通过 |
| 2026-08-28 | V1-3 上下文工程 | ①token 级预算（`chars/4` 估算，`ContextManager` 改 `max_tokens`）；②LLM 摘要压缩（裁剪的 exchange 先由 LLM 压成 bullet，保留结论/改动的文件/失败/未解决，`ReactLoop` 提供 summarizer）；③工具结果压缩（`compress_command_output` 只留头+错误/traceback/断言行+尾，原文归档到 `.coding-agent/artifacts/`）；④只读工具缓存（`ToolCache` 按 `tool+归一化参数+workspace_version` 缓存，写工具成功递增版本失效）+ `CACHE_HIT`/`tool_cache_hits` 指标；⑤交互命令 `/help /compact /clear /history`（`/compact` 参考 Claude Code：LLM 摘要整段历史）。全套 129 测试通过 |
| 2026-08-28 | eval 缓存指标 + 压力任务 | 前端结果卡/逐任务表/对比表增加 **Cache hits** 指标；新增压力任务 `stress_noise_extract`（跑 `noise.py` 输出 3000 行→触发 V1-3-3 压缩，从尾部提取 `FINAL_RESULT=42` 写入 result.txt，verify 确定性判定）。实测 45k 字符命令输出压缩到 ~340 字符且 FINAL_RESULT 保留。全套 131 测试通过 |
| 2026-08-28 | V2-5 动态委派 + 并行只读 SubAgent | ①`src/planning/delegation.py`：确定性 `DelegationPolicy`（`direct/delegate/parallel`），按「复杂度信号 + 依赖 + 描述长度」判简单步，按「角色读写性」判并行——可运行批内前导只读步并行、首个写步串行；②`src/agents/direct_worker.py`：无 `submit_report` 的轻量 ReAct 循环（全工具集），简单步省掉结构化汇报往返；③`MainAgent` 重构执行循环：批量取可运行步→策略路由→`ThreadPoolExecutor` 并行只读 SubAgent；④`EventBus.emit` 加锁串行化（多线程共享一 bus 安全）+ `DELEGATION` 事件 + `direct_steps/parallel_batches/parallel_steps` 指标；⑤eval 记录/聚合/前端增加 Direct/Parallel 列，新增 `parallel_summarize` 任务（3 个独立模块求和，诱发并行探索）。全套 151 测试通过 |
| 2026-08-29 | V2-5.1 指标打分替换关键词 | `_is_simple` 关键词门 → 数值 `complexity_score = 20·min(1,tok/40)+20·min(1,files/3)+15·min(1,deps/2)+15·is_write+40·verb_risk`（0–100，阈值 50），verb_risk 为粗粒度四类（read/create/fix/refactor）；分数随 `DELEGATION` 事件进 `avg_complexity` 指标。全套 156 测试，真实 eval 9/9（direct_steps=11，均 token 11251 vs 13287 基线） |
| 2026-08-29 | V2-5.2 编排三档 fast/auto/thorough | `src/orchestration.py` 枚举 + `--orchestration` CLI + eval `agent_mode` 别名（fast/single、auto/multi、thorough）；`DelegationPolicy(direct_enabled=False)` 让 thorough 永不 DIRECT（并行保留）；`build_single_agent` 上移到 src。全套 162 测试，dry-run 三档 direct 步 = 0/9/0 |
| 2026-08-29 | V2-5.3 direct-first 级联 | DIRECT 失败无条件升级 DELEGATE；边界带（score∈[40,50)）额外跑 LLM-as-judge 校验自述、判 NO 也升级；新增 `ESCALATE` 事件 + `escalations` 指标 + eval/前端 Esc 列。全套 168 测试，真实 eval 9/9（direct_steps=13、escalations=0、均 token 10038） |
| 2026-08-29 | V2-5.4 任务级 TaskRouter（退役步级 DIRECT） | 数据证明「任务级拓扑（fast vs multi）≈ 15×步级 direct 的收益」，故**删除 DirectWorker 与步级 direct/verify-judge**，`DelegationPolicy` 收敛为「并行只读调度器」；新增 `src/task_router.py`：`task_score = 25·min(1,tok/80)+25·min(1,files/5)+20·verb_risk+15·multi+15·test`（三档 band：<40 fast、40–65 fast-first+judge 升级 multi、≥65 multi），`auto` = TaskRouter(single,multi)。指标由 direct_steps/avg_complexity 换成 fast_routes/multi_routes/avg_task_score + escalations；eval dry-run LLM 改为按 tools 形状应答。全套 157 测试，真实 eval 9/9：**fast=8976 / auto=8832 / thorough=16980 token**，auto 逼近 fast（-48% vs thorough），Routes fast=7 multi=2、escalations=0 |
| 2026-08-29 | V2-6 Model Routing | `src/llm/router.py`：`TaskType` + `ModelRouter.route(task_type)` + `build_model_router`（读 `DEEPSEEK_FAST_MODEL`，单模型下 strong/fast 共用一 client）；映射 planning/coding→强，exploration/testing/summarization/synthesis→快；把 `summarizer_llm` 穿到 `ReactLoop`/`BaseAgent`/角色 agent（摘要走快模型）；`build_main_agent`/`build_single_agent`/`build_agent` 接受 router。全套 160 测试，dry-run 无回归 |
| 2026-08-29 | V2-7 Retrieval Memory | `src/memory/retrieval.py`：`RetrievalMemory` + `MemoryEntry`（task+summary+关键词+文件+时间+task_id）+ `extract_keywords`（英文词过滤停用词 + 中文 bigram）；新任务按关键词 Jaccard 打分取 Top-K 片段注入上下文；`MainAgentSession` 集成（send 前注入、send 后索引），`interactive` 持久化到 `.coding-agent/memory.json`。全套 167 测试通过 |
| 2026-08-29 | V2-5.5 /btw 并行提问（V2-5 延伸） | `src/session/side_quest.py`：①输入解耦——**主线程独占 stdin**（agent 跑在后台 daemon 线程，避免 Windows 后台线程 `input()` 吞 Ctrl+C 导致卡死），`/btw` 行路由到 `SideQuestQueue`；②checkpoint 投递——`ReactLoop`（每轮迭代）与 `MainAgent`（每步）各加 `checkpoint_cb` 钩子 drain 队列；③并行 vs 排队——只读 side 问题（`classify_side_quest` 判写信号）走 `SideQuestWorker`（只读 explorer 工具集 + 无汇报）在 `ThreadPoolExecutor` 与主 agent 并行，写 side 问题推迟到主任务结束后串行执行；`SideQuestCoordinator` 汇总并打印 `/btw` 答案；`/help` 增补 `/btw`。全套 174 测试通过 |
| 2026-08-29 | V2-8 Skill / Workflow（参考 Claude Code Agent Skills） | `skills/{fix-tests,code-review,implement-feature,refactor}/SKILL.md`：YAML frontmatter（`name/description/keywords/allowed_tools/verification/steps`）+ markdown body；`src/skills/registry.py`：`SkillRegistry`（解析 SKILL.md）+ `SkillMatcher`（确定性 keyword 匹配）+ **progressive disclosure**（Planner 只看 name+description catalog，命中才加载 body）；`MainAgent` 命中 skill 时用 `steps` 确定性模板直接建 TaskPlan（跳过 LLM Planner），`_build_subtask` 注入 skill guidance；新增 `SKILL_MATCHED` 事件 + `skill_matches` 指标 + eval/前端 Skill 列。全套 183 测试通过，dry-run thorough 命中 fix-tests（Skills=1） |
| 2026-08-29 | V2-8.1/V2-8.2 Skill 自定义 + 解耦 planning | ①三层目录（`~/.coding-agent/skills` > `<ws>/.coding-agent/skills` > 内置）+ 同名覆盖 + 校验；②交互命令 `/skills` `/skill` `/use`（强制下一条任务）；③**`steps` 改为可选**——带 steps=工作流模板（multi 确定性规划），不带 steps=guidance 型（正文/verification/allowed_tools 注入）；④**fast 单 agent 也能用 skill**：`BaseAgent` 加 `skill_registry`，命中则把 guidance 前置到 task，无需 planner。全套 190 测试通过 |
| 2026-08-30 | V2-9 MCP（Model Context Protocol） | ①`src/mcp/client.py` 零依赖 stdio JSON-RPC 客户端（`initialize`→`tools/list`→`tools/call`），后台读线程 + queue 实现跨平台超时，Windows `.cmd/.bat` shim 自动走 shell；②`src/mcp/config.py` 解析 `<ws>/.coding-agent/mcp.json`；③`src/mcp/manager.py` 把每个 server 的工具包装成 `mcp__<server>__<tool>` 的 `ToolDefinition`，失败 server 记录后跳过；④`extra_tools` 注入通道穿到 fast/auto/thorough 全部角色 agent，复用 `ToolExecutor`（校验/权限/事件/审计/错误归一）；⑤权限：`mcp__*` 基础风险=3（等同 shell），default 模式每次调用审批；⑥CLI `--mcp-config` + 启动打印已发现工具；⑦示例 `examples/mcp_servers/echo_server.py`（echo/now/add 三工具）+ `mcp.example.json`，零 npm/网络即可演示。全套 **205 测试通过**（新增 14）。后续实测接真实 `mcp-server-fetch`（robots.txt 拦截→`--ignore-robots-txt`）与 `@modelcontextprotocol/server-github`（26 工具，`GITHUB_PERSONAL_ACCESS_TOKEN`），并修复 isError 错误透传 + 启动 stderr 透传 |
| 2026-08-30 | V3-10 会话持久化 + Session 管理 | ①`src/session/store.py`：`load_session`（缺失/损坏 fail-open 回退空会话）/`save_session`（版本号 + workspace + updated_at + history + last_plan）/`default_session_path`；②`MainAgentSession` 增 `set_history`/`set_last_plan`/`last_plan` 属性 + `send()` 自动捕获上次 plan 快照 + `/session` `/new` 命令；③`interactive()` 启动 load（打印 resumed N entries）、每轮结束 + finally 退出时 save（崩溃最多丢一轮）；④**取舍**：会话续聊 ≠ 续跑到一半的 plan（不序列化半路 TaskPlan/WorkspaceContext）。全套 **214 测试通过**（新增 9） |
| 2026-08-30 | V3-9a LLM token 流式 | `StreamChunk` + `LLMClient.chat_stream`（默认 fallback 到 `chat`，mock 兼容）；`DeepSeekClient.chat_stream`（`stream=True`+`include_usage`，按 index 拼装 tool_calls）；`STREAM_DELTA` 事件；`ReactLoop` 增 `streaming` 开关，`_call_llm` 流式路径发 delta 并重组 `LLMResponse`；`streaming` 穿到 build_agent/各角色 agent。全套 217 测试通过 |
| 2026-08-30 | V3-9b Web 后端 | `web/broker.py`：`EventBroker`（EventBus 消费者，fan-out 到 SSE 订阅者 + 有界 replay）+ `WebApprover`（阻塞式审批：`APPROVAL_PENDING` + id，按工具「始终允许」+ 超时自动拒绝）；`web/server.py`：FastAPI——`POST /api/sessions` 后台线程跑 agent（skills+MCP+streaming+web approver）、SSE `/api/sessions/{id}/events`、`POST .../approve/{id}`、list/get；`APPROVAL_PENDING` 事件；`approver` 参数穿到 build_agent（Web 注入而非 stdin）。全套 224 测试通过 |
| 2026-08-30 | V3-9c Web 前端 + 流式最终回答 | `web/static/index.html` 单文件暗色 UI（零构建）：任务表单、可折叠 step（`<details>`+状态徽章）、每步工具轨迹（patch/write 显示 diff）、流式回答面板、固定审批横幅（允许一次/始终允许/拒绝）、自动滚动；`STREAM_DELTA` 按 agent_id 路由（main_agent/single_agent→回答面板）；`ToolExecutor` 事件带 `tool_call_id` 关联结果/错误；`MainAgent._synthesize` 走 `chat_stream` 流式发最终回答（无 stream 的 mock 回退 `chat`）；`SESSION_END` 带 summary。**实测端到端**：POST 任务→agent 跑（list_files/read_file）→SSE 实时推 token 级 delta |
| 2026-08-30 | V3-9d CLI 体验优化 | `ConsoleTracer` 重写：`--quiet`（只留步骤级 + 最终回答，隐藏 LOOP_STEP/PRE/POST_TOOL_USE/LLM_CALL）、`--no-color`、ANSI 彩色状态（成功绿/失败红/进行黄/工具名青）、`▶` 步骤头、结果截断可配（默认 4000）。全套 **224 测试通过** |
| 2026-08-30 | V3-9 重构：会话持久化 + Harness 风格布局 | ①**修复历史丢失**：`web/store.py` 把每个会话（messages+events+meta）落盘 `.coding-agent/web-sessions/<id>.json`，刷新/重启都不丢（实测重启后仍可加载）；②`web/server.py` 重写：`/api/workspaces`（项目子目录）、`POST /sessions`（建会话）、`POST /sessions/{id}/messages`（**多轮对话**，懒建 agent + 复用 `MainAgentSession`）、`DELETE`、SSE（replay+live）；③`TraceEvent.from_dict` + `EventBroker.replay` 冷会话回温、`TURN_END` 事件；④前端重写：左侧栏（工作区选择 + 会话列表 + 新建/删除），右侧「对话/轨迹」双视图切换——对话气泡流式输出 + 可折叠轨迹 + diff + 审批横幅。全套 **233 测试通过**（新增 9），端到端实测（多轮 + 审批 + 重启持久化） |
| 2026-08-30 | V3-11 性能 Dashboard | ①`MetricsCollector` 增 prompt/completion 分词 + `cost_usd`（`DEEPSEEK_INPUT_PRICE`/`DEEPSEEK_OUTPUT_PRICE` 每 1M token 价可配，默认 deepseek-chat 0.27/1.10）+ `snapshot`/`reset`；②新增 `SessionMetrics`（会话聚合 + 每次任务快照）；③CLI：`print_metrics` 显示 tokens in/out + cost，`print_task_metrics` 打印每任务表（calls/tok_in/tok_out/cost/ms），`interactive` 每轮 `finish_task`、one-shot 结束也记一条；④Web：每会话挂 `SessionMetrics`，`GET /api/sessions/{id}/metrics`（冷会话从持久化事件重算聚合+每轮），前端新增「指标」页（聚合卡片 + 每任务表）。全套 **243 测试通过**（新增 5） |
| 2026-09-01 | 移除成本展示 + /compact 上下文联动 | ①删除 `MetricsCollector` 的 `cost_usd`/`_cost`/`_env_price` 与 `.env` 价格项，CLI `print_metrics`/`print_task_metrics` 及 Web 指标条/指标页去掉成本列，测试同步更新；②`/compact` 压缩后 emit `CONTEXT_COMPACT`（携带 `removed_chars`），`ContextTracker` 与 Web 前端按 `removed_chars//4` 下调上下文占用显示，压缩后 `[ctx ...]` 立即变小 |
| 2026-09-01 | fast 模式补最终总结 + 语言匹配 | fast（单 ReAct 循环）此前没有 Planner/Supervisor，最终回答 = 模型最后一句话的原样文本（如英文的 "All 4 tests pass."），既无总结也不随用户中文回答。现 `BaseAgent` 增 `synthesize_final`/`synthesis_llm`：循环结束后用 `_synthesize_final_answer` 把原始结果重写为用户语言的简洁回答（`_answer_language` 判中文→中文），并关闭循环自身的流式、只让总结流式输出（Web 回答面板只显示成品答案，不显示中间英文旁白）；`build_single_agent` 打开该开关。新增 1 测试，全套 **247 测试通过** |
| 2026-09-01 | 打包为 `pcoding` 命令 + 启动目录即工作区 + Web 工作区列表 | ①新增 `pyproject.toml`（`[project.scripts] pcoding = "src.main:main"`，打包 `src`+`web`），`pip install -e .` 后可用 `pcoding` / `pcoding "task"` / `pcoding web`；②CLI 默认 `--workspace` 改为 `.`（启动目录），Web 默认工作区 = `LAUNCH_DIR`（`Path.cwd()`）；③Web 后端新增工作区注册表（`.coding-agent/workspaces.json`）+ `GET/POST /api/workspaces`（启动目录 + 已添加 + 会话目录去重）；④前端左侧栏去掉工作区下拉框，改为「列出所有工作区、每个工作区下挂各自会话 + 每工作区一个＋新建」；「添加工作区」经目录浏览器选中后 `POST /api/workspaces` 持久化。新增 2 测试，全套 **251 测试通过** |
