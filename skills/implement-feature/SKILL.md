---
name: implement-feature
description: Implement a new feature from a specification. Use when the user asks to implement or add a feature.
keywords:
  - implement
  - add feature
  - 实现
  - 新增功能
allowed_tools:
  - list_files
  - read_file
  - search_text
  - patch_file
  - write_file
  - execute_command
verification: The feature works and its tests pass.
steps:
  - agent: explorer
    description: Understand the existing code and where the new feature fits.
  - agent: coding
    description: Implement the feature and add or update tests.
  - agent: test
    description: Run the tests to verify the feature works.
---

Understand before implementing, implement the feature, then verify with tests.
Keep the change minimal and do not break existing behavior.
