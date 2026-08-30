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

## MCP — Model Context Protocol（待确认后实现）

> 状态：`🚧 方案待确认`（用户确认后再动工，编号暂称 V2-9）

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

1. stdio client + `tools/list` / `tools/call` + 注册进 registry（含审批）。
2. CLI `--mcp-config` + 启动时打印已发现的 MCP 工具。
3. 多 server、超时/崩溃兜底、工具名冲突前缀。
4. （可选）SSE/HTTP 远程传输。

### 风险/取舍

- 每调一次 MCP 工具 = 一次进程间 JSON-RPC 往返，比内置函数慢。
- MCP server 是用户显式授权的代码执行，硬 DENY 清单管不到其内部，安全边界只能靠「高基础风险 + 默认审批」这一层守卫。

---

## V3 — 产品化

### 9. Web Agent Workspace

- **状态**：`- [ ]`
- **具体做什么**：FastAPI + SSE/WebSocket 把 Event Bus 事件推到前端；渲染 Plan Timeline、Agent Trace、Approval UI（Approve/Reject/Always allow）、Diff Viewer（复用 PatchReport.modified_files）。
- **达到什么效果**：一个非聊天式的「Agent Workspace」，可实时看计划进度、工具轨迹、代码 diff，并对审批作出回应。

### 10. 会话持久化 + Session 管理

- **状态**：`- [ ]`
- **具体做什么**：按 workspace 保存会话（消息 + WorkspaceContext + 计划状态）到 `.coding-agent/session.json`；启动加载、退出保存；支持 resume；不同项目目录各自独立会话。
- **达到什么效果**：跨进程/跨天续聊；每个项目目录有独立、可恢复的会话历史。

### 11. 性能 Dashboard

- **状态**：`- [ ]`
- **具体做什么**：基于 V1-2 的 MetricsCollector，聚合 token/耗时/成本/成功率，输出会话级与任务级报表（CLI 或 Web）。
- **达到什么效果**：一眼看清 agent 的资源消耗与效率，支撑"性能优化"的量化论证。

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
