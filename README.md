# Coding Agent（推免考核项目）

一个从零实现的编程智能体（coding agent）：通过大语言模型原生 tool calling，自主完成
「搜索/读取代码 → 执行命令/测试 → 修改文件 → 复跑验证」的闭环。不使用任何 Agent 框架，
核心机制（上下文管理、工具定义与本地执行、输出解析、循环终止、错误处理）全部自行实现。

## 快速开始

1. 安装依赖

   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   pip install -r requirements.txt
   ```

2. 配置密钥

   复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY`（该文件已被 gitignore，不会入库）。

3. 运行

   ```
   python -m src.main                # 交互模式（多轮记忆）：输入任务，回车执行；exit 退出
   python -m src.main "你的任务"       # 单次执行（脚本 / 演示用）
   python -m src.main "任务" --mode plan   # 计划执行（Main Agent：规划 + 分步 + 重规划）
   ```

   默认工作区为 `demo_workspace`，可用 `--workspace <目录>` 指定其它目录。

## 运行测试（无需 API key，用 Mock LLM）

```
python -m unittest discover -s tests -v
```

## 目录结构

```
src/
├── main.py               # 入口：装配 LLM / 工具 / 循环并运行（交互模式）
├── core/                 # 数据模型 + 显式状态机 + 终止策略
├── context/              # ContextManager（上下文裁剪）+ Session（多轮记忆）
├── llm/                  # LLMClient 接口 + DeepSeekClient
├── tools/                # 工具定义 / 注册 / 校验 / 执行 + 本地工具
├── safety/               # 工作区边界守卫
├── loops/                # ReAct 循环（行动层）
├── planning/             # TaskPlan / Planner / Replanner（任务层）
└── agents/               # MainAgent（Plan-and-Execute 主管）
```

## 架构要点

- 单 Agent ReAct Loop：原生 tool calling + 显式状态机（`RUNNING → DONE / FAILED / MAX_STEPS`）。
- 工具调用只是模型发出的"请求"，真正的执行由本地 `ToolExecutor` 完成。
- 工具集：`list_files` / `read_file` / `search_text` / `patch_file` / `write_file` / `execute_command`。
- 执行前做参数校验（必填 + 类型），`patch_file` 写前校验 `old_text` 唯一性。
- 所有文件操作经 `workspace_guard` 限定在工作区内（含符号链接逃逸防护）。
- 工具异常统一转成结构化 `ToolResult` 回传给模型，而非直接崩溃。
- `ContextManager`：消息超预算时裁剪最旧的工具交互，保留系统提示 + 任务 + 近期。
- 终止策略：Max Steps + 重复操作检测（`tool_name + 归一化参数`）+ 连续工具错误，先警告反馈、再确定性终止。
- `Session`：交互模式内跨 turn 共享上下文（多轮记忆）；每项目磁盘持久化推迟到 Phase 5 后。
- 双层循环：任务层 `Plan → Dispatch → Execute → Observe → Replan`（`MainAgent`）+ 行动层 ReAct（`ReactLoop`）。
- `Planner` / `Replanner`：用原生 tool calling 的 `submit_plan` 返回结构化计划；重规划只改未完成部分，保留已完成结果。

## Roadmap

- Phase 1：最小闭环 ✅
- Phase 2：`search_text` / `patch_file` / `write_file` + 参数校验 + 命令超时 ✅
- Phase 3：上下文管理 + 终止条件（重复操作检测、错误恢复）✅
- Phase 4（当前）：Plan-and-Execute + 动态重规划 ✅
- Phase 5：Main Agent + Explorer / Coding / Test 多 Agent 协作
