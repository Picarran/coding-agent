# Skills 使用说明（V2-8）

Skill 是「可复用的工作流模板」，参考 Claude Code Agent Skills。一个 skill 就是一个**文件夹 + `SKILL.md`**：命中后 Main Agent 直接按模板里的固定步骤执行，跳过从零规划，更稳定、更可预期。

## 一、Skill 长什么样

```markdown
---
name: fix-tests
description: Fix failing tests so the whole suite passes.
keywords:
  - fix test
  - failing test
  - 修复测试
  - 测试失败
allowed_tools:
  - list_files
  - read_file
  - search_text
  - patch_file
  - execute_command
verification: All tests pass (exit code 0).
steps:
  - agent: test
    description: Run the test suite and report which tests fail.
  - agent: explorer
    description: Read the failing test and the code it exercises.
  - agent: coding
    description: Fix the code so the failing test passes.
  - agent: test
    description: Re-run the tests to confirm they all pass.
---

（正文是给执行 agent 看的自由指引，可选。）
```

### frontmatter 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 唯一名，目录名默认 |
| `description` | 是 | 一句话描述（进 Planner 的 catalog） |
| `steps` | 是 | 固定步骤列表，每项 `agent`（explorer/coding/test）+ `description` |
| `keywords` | 否 | 自动匹配关键词（命中任一即匹配） |
| `allowed_tools` | 否 | 工具约束（注入 guidance，作为软约束） |
| `verification` | 否 | 完成判据，注入每个步骤的 guidance |

## 二、放哪里（三层目录，同名后者覆盖前者）

```
优先级 高 → 低：
  1. ~/.coding-agent/skills/           个人全局，处处可用
  2. <workspace>/.coding-agent/skills/ 项目级，可提交共享
  3. <项目>/skills/                    内置（fix-tests / code-review / implement-feature / refactor）
```

**添加一个 skill = 往上面任一目录丢一个 `SKILL.md` 文件夹，零代码、无需重启**（每次任务自动重载）。

## 三、怎么用

### 1. 自动匹配（默认）

任务文本命中某 skill 的 `keywords`，就自动走它的模板。例如：

```
> 修复失败的测试，让 test_calculator.py 通过
（命中 fix-tests → 按 test→explorer→coding→test 四步执行）
```

### 2. 显式触发（交互模式斜杠命令）

| 命令 | 作用 |
|---|---|
| `/skills` | 列出所有可用 skill（name + description） |
| `/skill <name>` | 查看某个 skill 的步骤和指引 |
| `/use <name>` | 强制**下一条任务**用该 skill（一次性） |

```
> /skills
  fix-tests         Fix failing tests...
  code-review       Review a change...
> /use code-review
  Next task will use skill 'code-review'.
> 审查一下 src/agents 里的代码
（强制走 code-review 模板，跳过关键词匹配）
```

### 3. 校验

加载时逐条校验 SKILL.md，缺 `name`/`description`/`steps` 的坏 skill 会被**跳过并打印 warning**，不影响其它 skill。

## 四、内置 skill 一览

- `fix-tests`：修失败的测试（复现→定位→修复→回归）。
- `code-review`：审查代码并逐条给出文件/行级问题。
- `implement-feature`：实现新功能（理解→实现→测试验证）。
- `refactor`：行为不变的重构（理解→重构→测试回归）。
