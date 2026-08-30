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
   python -m src.main "你的任务"      # 单次执行（多 Agent 编排）
   python -m src.main                 # 交互模式（多 Agent，多轮）
   ```

   默认工作区为 `demo_workspace`，可用 `--workspace <目录>` 指定其它目录。

## 运行测试（无需 API key，用 Mock LLM）

```
python -m unittest discover -s tests -v
```

## 目录结构

```
src/
├── main.py               # 入口：装配并运行 Main Agent（交互 / 单次）
├── core/                 # 数据模型 + 显式状态机 + 终止策略
├── context/              # ContextManager + WorkspaceContext + 环境上下文
├── llm/                  # LLMClient 接口 + DeepSeekClient
├── tools/                # 工具定义 / 注册 / 校验 / 执行 + 本地工具
├── safety/               # 工作区边界守卫 + 权限/风险引擎
├── loops/                # ReAct 循环（行动层）
├── planning/             # TaskPlan / Planner / Replanner（任务层）
├── agents/               # MainAgent + Explorer/Coding/Test SubAgent + 结构化报告
├── mcp/                  # MCP 客户端/配置/管理器（V2-9，外部工具接入）
├── skills/               # Skill 注册表 + 匹配器（V2-8）
└── session/              # 会话持久化（V3-10，session.json）
web/                      # Web Agent Workspace（V3-9，FastAPI + SSE + 前端）
examples/mcp_servers/     # 示例 MCP server（echo/now/add）+ 示例配置
```

## 架构要点

- 行动层 ReAct Loop：原生 tool calling + 显式状态机（`RUNNING → DONE / FAILED / MAX_STEPS`）。
- 工具调用只是模型发出的"请求"，真正的执行由本地 `ToolExecutor` 完成。
- 工具集：`list_files` / `read_file` / `search_text` / `patch_file` / `write_file` / `execute_command`。
- 执行前做参数校验（必填 + 类型），`patch_file` 写前校验 `old_text` 唯一性。
- 所有文件操作经 `workspace_guard` 限定在工作区内（含符号链接逃逸防护）。
- 工具异常统一转成结构化 `ToolResult` 回传给模型，而非直接崩溃。
- `ContextManager`：消息超预算时裁剪最旧的工具交互，保留系统提示 + 任务 + 近期。
- 终止策略：Max Steps + 重复操作检测（`tool_name + 归一化参数`）+ 连续工具错误，先警告反馈、再确定性终止。
- `MainAgentSession`：交互模式下跨 turn 携带对话上下文。
- 最终回答语言与用户输入一致（检测到中文则用中文回答）。
- 双层循环：任务层 `Plan → Dispatch → Execute → Observe → Replan`（`MainAgent`）+ 行动层 ReAct（`ReactLoop`）。
- `Planner` / `Replanner`：用原生 tool calling 的 `submit_plan` 返回结构化计划；重规划只改未完成部分，保留已完成结果。
- Supervisor-Worker：`MainAgent` 按步骤类型分派 `ExplorerAgent`（只读）/ `CodingAgent`（全工具）/ `TestAgent`（取证），SubAgent 无权自建新 SubAgent。
- 工具权限隔离：Explorer/Test 无 `patch_file` / `write_file`，由 `ToolRegistry` 确定性控制，不靠 prompt 约束。
- 结构化产物通信：每个 SubAgent 用 `submit_report` 返回结构化报告（InvestigationReport / PatchReport / TestReport），只回传 Summary + 结构化 artifacts，不传完整历史。
- 环境上下文：启动时采集 OS/Shell/Python/工作区并注入所有 System Prompt，避免跨平台命令错误（如 Windows 上误用 `ls`）。
- 最终回答合成：Main Agent 完成后用 LLM 综合各 SubAgent 报告，输出面向用户的自然语言回答，而非"All steps completed"。
- `WorkspaceContext`：跨步骤只传递「已读文件/已改文件/关键发现/测试结果」紧凑块，避免重复探索。
- `execute_command` 平台感知解码（修中文乱码）；`list_files` 过滤 `__pycache__`/`.pyc`/隐藏文件。

## MCP 外部工具（Model Context Protocol）

通过 MCP 把外部工具服务器（GitHub、文件系统、数据库、浏览器…）接入 agent，工具**即插即用**，无需改 agent 代码。每个 MCP 工具以 `mcp__<server>__<tool>` 名字暴露给模型，走与内置工具相同的 `ToolExecutor`（参数校验 / 权限审批 / 事件审计）。

- 默认配置：`<workspace>/.coding-agent/mcp.json`；也可用 `--mcp-config <path>` 指定。
- 无配置文件时自动跳过（打印 `MCP: no config ...`）。
- 每个 server 是一个进程，通过 stdio 用 JSON-RPC 通信；`tools/list` 发现工具、`tools/call` 执行。

配置格式（`command` = 可执行文件，`args` = 参数，`env`/`timeout` 可选）：

```json
{
  "servers": {
    "<名字>": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/path/to/dir"],
      "env": {},
      "timeout": 60
    }
  }
}
```

### 例子 1：本地示例 server（零依赖、无需网络）

仓库自带一个最小 MCP server（`echo` / `now` / `add` 三个工具）。把 `examples/mcp_servers/mcp.example.json` 复制为工作区的 `.coding-agent/mcp.json`，或在项目根目录直接：

```json
// demo_workspace/.coding-agent/mcp.json
{
  "servers": {
    "demo": { "command": "python", "args": ["examples/mcp_servers/echo_server.py"] }
  }
}
```

在**项目根目录**运行（相对路径按 agent 的工作目录解析）：

```
python -m src.main "用 MCP 的 add 工具算 2 加 3，再用 now 告诉我现在几点" --workspace demo_workspace
```

启动时会打印发现的工具：

```
MCP: 3 tool(s) from ...\demo_workspace\.coding-agent\mcp.json
  demo: mcp__demo__echo, mcp__demo__now, mcp__demo__add
```

### 例子 2：官方 filesystem server（npx）

```json
{
  "servers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/my/project"]
    }
  }
}
```

### 例子 3：GitHub server（创建仓库 / 提交，已实测）

官方 `@modelcontextprotocol/server-github`，暴露 **26 个工具**（`mcp__github__*`），包括 `create_repository`、`push_files`、`create_or_update_file` 等。

**第 1 步 —— 准备 token**：到 `github.com/settings/tokens` 建一个 token（细粒度 token 勾选 *Contents* 读写 + *Administration* 读写；或经典 token 勾 `repo` scope），写进 `.env`（`.env` 已被 gitignore，token 不会入库）：

```
GITHUB_PERSONAL_ACCESS_TOKEN=github_pat_...
```

**第 2 步 —— 配置**（token 从 `.env` 读取，客户端用 `load_dotenv` 后的环境变量继承给 server 子进程，所以 `mcp.json` 里不用写 token）：

```json
{
  "servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"]
    }
  }
}
```

**第 3 步 —— 使用**（关键工具及参数）：

- `mcp__github__create_repository`：`name`（必填）、`description`、`private`、`autoInit`（是否初始化 README）。
- `mcp__github__push_files`：一次性推送多个文件并**提交**——`owner`、`repo`、`branch`、`message`、`files`（`[{path, content}]`，均必填）。
- `mcp__github__create_or_update_file`：单文件创建/更新——`owner`、`repo`、`path`、`content`、`message`、`branch` 必填，`sha` 更新已有文件时必填。

```bash
# 建一个仓库，并提交一个 hello.py
python -m src.main "用 GitHub 工具：先创建仓库 my-demo-repo（public、带 README），然后 push 一个 hello.py（内容 print('hello')）到 main 分支，提交信息 init" --workspace demo_workspace --permission autonomous
```

启动时会打印 `github: mcp__github__create_repository, mcp__github__push_files, ...` 共 26 个。首次 `npx` 会联网下载包（约几十秒），之后有缓存。

> 安全提示：这是**可写外部账号**的操作（创建仓库/提交会真实改动你的 GitHub），基础风险分同 `mcp__*` = 3；建议只在明确授权时用 `autonomous`，否则每次调用都会弹审批。

### 例子 4：网页抓取 `mcp-server-fetch`（已实测）

抓取网页并把 HTML 转成 markdown。推荐用 `uvx` 直接跑（本机已装 uv，无需 `pip install`）：

```json
{
  "servers": {
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    }
  }
}
```

也可用 pip 方式：先 `pip install mcp-server-fetch`，再 `{"command": "python", "args": ["-m", "mcp_server_fetch"]}`。

实测 `tools/list` 会发现一个工具 `mcp__fetch__fetch`，参数为 `url`（必填）、`max_length`（默认 5000）、`start_index`（默认 0，用于分块读取长网页）、`raw`（默认 false，`true` 则不做 markdown 转换）。

示例任务（`--permission autonomous` 以免每次调用都弹审批）：

```
python -m src.main "抓取 https://example.com 的标题和第一段内容，用中文总结" --workspace demo_workspace --permission autonomous
```

注意：`uvx` **首次运行会联网下载依赖（约 40–60 秒）**，之后有缓存则很快；本客户端已把 `initialize`/`tools/list` 的启动超时放宽到 120 秒以兼容冷启动。

**robots.txt**：该 server 默认遵守网站的 robots.txt——由模型发起的请求，若目标站禁止爬取（如 baidu.com），会返回错误。两种处理：

- 只抓允许爬取的站（如 `example.com`），保持默认；
- 在 `args` 里加 `--ignore-robots-txt` 忽略限制（仅用于你有权抓取的页面）：

```json
{ "fetch": { "command": "uvx", "args": ["mcp-server-fetch", "--ignore-robots-txt"] } }
```

也可加 `--user-agent=YourUA` 自定义 UA。

### 安全说明

MCP server 是**用户显式授权的代码执行**：它想跑什么就能跑什么，内置的危险命令 DENY 清单管不到它内部。因此 `mcp__*` 工具的基础风险分设成 **3**（与任意 shell `execute_command` 同级）：

- `default` / `safe` / `plan` 模式下，**每次调用 MCP 工具都会弹审批**；`plan` 模式（只读白名单）直接确定性拒绝。
- 只有 `autonomous` 模式放行（`--permission autonomous`）。

## Web Agent Workspace（实时看 agent 工作）

```
python -m uvicorn web.server:app --port 8001
```

浏览器打开 `http://127.0.0.1:8001`。界面分三块：

- **左侧栏**：选择工作目录 + 该目录下的会话列表（每个目录可开多个会话，可新建/删除）；会话**持久化到磁盘**，刷新或重启服务都不丢历史。
- **右侧「对话」视图**：像聊天一样与 agent 多轮交互，回答 token 级流式输出。
- **右侧「轨迹」视图**：切换到「轨迹」标签，实时看计划步骤逐条推进、每个 step 里的工具调用（可折叠、`patch_file`/`write_file` 显示 diff）。

遇到需审批的操作（命令/MCP）会弹出「允许一次 / 始终允许 / 拒绝」。

## CLI 体验（V3-9 同批优化）

- `--quiet`：只显示步骤级进度 + 最终回答，隐藏每次工具调用的细节（适合长任务不刷屏）。
- `--no-color`：关闭 ANSI 彩色（成功绿 / 失败红 / 进行黄 / 工具名青）。
- 交互命令新增 `/session`（查看当前会话与上次计划）、`/new`（重开一个干净会话）。

## 会话持久化（V3-10）

交互模式下会话按 workspace 自动保存到 `<workspace>/.coding-agent/session.json`：启动时自动 resume（打印 `Resumed session: N history entries`），每轮结束与退出时保存，崩溃最多丢当前一轮。`/new` 重开、`/session` 查看。这是「会话续聊」而非「续跑到一半的 plan」——上次计划仅作快照展示，新任务从零规划。

## Roadmap

- Phase 1：最小闭环 ✅
- Phase 2：`search_text` / `patch_file` / `write_file` + 参数校验 + 命令超时 ✅
- Phase 3：上下文管理 + 终止条件（重复操作检测、错误恢复）✅
- Phase 4：Plan-and-Execute + 动态重规划 ✅
- Phase 5（当前）：Main Agent + Explorer / Coding / Test 多 Agent 协作 ✅
