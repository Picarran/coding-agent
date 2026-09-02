PAgent —— 从零实现的编程智能体
================================================================

一、Git 仓库地址
https://github.com/Picarran/coding-agent

二、如何运行
1. 安装与密钥
   python -m venv .venv
   .venv\Scripts\activate            （Windows）
   pip install -e .
   复制 .env.example 为 .env，填入 DEEPSEEK_API_KEY。
2. 命令行（默认工作目录 = 启动目录）
   pcoding
   常用参数：--workspace、--permission、--orchestration、--quiet、--mcp-config。
3. Web 工作台
   cd web/frontend && npm install && npm run build && cd ../..
   pcoding web           # 打开 http://127.0.0.1:8001
4. 测试（无需密钥，用 Mock LLM）
   python -m unittest discover -s tests
5. 评估平台（可选，独立于 Web 工作台）
   python -m uvicorn eval.server:app --port 8000   # 评测界面 http://127.0.0.1:8000

三、特色功能
· 多 Agent 编排：Plan→Dispatch→Execute→Observe→Replan 主循环，
  下辖 Explorer/Coding/Test 子 Agent，每个 Agent 内部 ReAct loop实现；
  支持按任务复杂度自动选择单/多 Agent。
· 权限引擎：plan/safe/default/autonomous 四档，风险评分 + 危险命令用户审批
  硬拒绝；可写/命令操作在交互与 Web 端弹审批。
· 可观测：统一事件总线，JSONL 审计日志 + 性能面板（token/耗时等）。
· 上下文工程：token 预算 + LLM 摘要压缩 + 工具结果压缩 + 关键工作记忆共享 + 只读缓存。
· MCP：可接入 GitHub、网页抓取等外部工具服务器，即插即用。
· Skill：可添加fix-tests/code-review 等工作流模板，命中即确定性执行。
· 流式输出、会话持久化、实时上下文占用显示。
· Agent评估平台：性能分析，基于 baseline 评测与优化 Agent。

四、其它说明
· 参考 Claude Code / Codex 的工程化思路，核心机制（状态机、工具执行、
  上下文管理、循环终止）全部自行实现，不依赖任何 Agent 框架。
· .env 已被 gitignore，密钥不入库。
· 完整设计文档见 README.md，设计取舍与演进见 ROADMAP.md。
