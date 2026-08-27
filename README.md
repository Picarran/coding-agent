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

3. 运行演示

   ```
   python -m src.main
   ```

   默认让 agent 自主修复 `demo_workspace` 中带 bug 的示例并通过测试；
   也可自定义：`python -m src.main "你的任务" --workspace <目录>`。

## 运行测试（无需 API key，用 Mock LLM）

```
python -m unittest discover -s tests -v
```

## 目录结构

```
src/
├── main.py               # 入口：装配 LLM / 工具 / 循环并运行
├── core/                 # 数据模型 + 显式状态机
├── llm/                  # LLMClient 接口 + DeepSeekClient
├── tools/                # 工具定义 / 注册 / 执行 + 本地工具
├── safety/               # 工作区边界守卫
└── loops/                # ReAct 循环
```

## 架构要点

- 单 Agent ReAct Loop：原生 tool calling + 显式状态机（`RUNNING → DONE / FAILED / MAX_STEPS`）。
- 工具调用只是模型发出的"请求"，真正的执行由本地 `ToolExecutor` 完成。
- 工具集：`list_files` / `read_file` / `search_text` / `patch_file` / `write_file` / `execute_command`。
- 执行前做参数校验（必填 + 类型），`patch_file` 写前校验 `old_text` 唯一性。
- 所有文件操作经 `workspace_guard` 限定在工作区内（含符号链接逃逸防护）。
- 工具异常统一转成结构化 `ToolResult` 回传给模型，而非直接崩溃。

## Roadmap

- Phase 1：最小闭环 ✅
- Phase 2（当前）：`search_text` / `patch_file` / `write_file` + 参数校验 + 命令超时 ✅
- Phase 3：上下文管理与终止条件（重复操作检测、错误恢复）
- Phase 4：Plan-and-Execute + 动态重规划
- Phase 5：Main Agent + Explorer / Coding / Test 多 Agent 协作
