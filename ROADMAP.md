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

- **状态**：`- [ ]`
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

- **状态**：`- [ ]`
- **具体做什么**：实现 `DelegationPolicy`（按复杂度/独立性/上下文规模决定 `direct / delegate / parallel delegate`）；只读 SubAgent（Explorer/Test）允许并行，写 SubAgent（Coding）串行执行。
- **达到什么效果**：简单步骤不再无谓创建 SubAgent；相互独立的探索可并行，缩短总耗时、减少无效 LLM 往返。

### 6. Model Routing

- **状态**：`- [ ]`
- **具体做什么**：预留 `ModelRouter.route(task_type)` 接口；Planner/复杂 coding 用强模型，Explorer/摘要/测试解析用快模型；当前单模型下接口可通、后续可插多模型。
- **达到什么效果**：复杂步骤不失智，简单步骤更省 token/延迟；架构支持多模型而不改上层。

### 7. Retrieval Memory

- **状态**：`- [ ]`
- **具体做什么**：历史 Artifact（报告/结论）按「关键词 + 文件路径 + 时间 + 任务ID」建索引；新任务查询时取 Top-K 相关片段注入上下文（先不引入向量库）。
- **达到什么效果**：跨任务能检索到"之前分析过的结论"，避免重复探索；上下文只注入相关片段而非全部历史。

### 8. Skill / Workflow

- **状态**：`- [ ]`
- **具体做什么**：`skills/` 定义 `fix-tests` / `code-review` / `implement-feature` / `refactor` 等工作流模板（工具约束 + 步骤顺序 + 验证策略）；Main Agent 先做 Skill 匹配，命中则按模板执行。
- **达到什么效果**：高频任务走确定性模板，比每次让 Planner 从零规划更稳定、更可预期。

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
