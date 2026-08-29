---
name: fix-tests
description: Fix failing tests so the whole suite passes. Use when the user asks to fix tests, make tests pass, or repair a failing test.
keywords:
  - fix test
  - failing test
  - test fail
  - make test pass
  - 修复测试
  - 测试失败
  - 测试通过
  - 让测试
  - 测试
allowed_tools:
  - list_files
  - read_file
  - search_text
  - patch_file
  - execute_command
verification: All tests pass (exit code 0).
steps:
  - agent: test
    description: Run the test suite and report exactly which tests fail.
  - agent: explorer
    description: Read the failing test and the code it exercises to locate the root cause.
  - agent: coding
    description: Fix the code so the failing test passes without breaking others.
  - agent: test
    description: Re-run the tests to confirm they all pass.
---

Fix the failing tests in this order: reproduce, diagnose, fix, verify. Do not
skip the final verification step; the workflow is only done when the tests pass.
