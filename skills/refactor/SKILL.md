---
name: refactor
description: Refactor existing code without changing its behavior. Use when the user asks to refactor, extract, or restructure code.
keywords:
  - refactor
  - extract
  - 重构
  - 拆分
  - 抽取
allowed_tools:
  - list_files
  - read_file
  - search_text
  - patch_file
  - write_file
  - execute_command
verification: All tests still pass (behavior unchanged).
steps:
  - agent: explorer
    description: Understand the code and its callers before changing anything.
  - agent: coding
    description: Perform the refactor (extract, move, or rename) and update every reference.
  - agent: test
    description: Run the tests to confirm behavior is unchanged.
---

Refactor in small, behavior-preserving steps and update every reference. Do not
add features while refactoring; the only goal is a cleaner structure with the
same behavior.
