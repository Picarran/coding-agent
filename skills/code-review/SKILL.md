---
name: code-review
description: Review a change or file and report concrete issues. Use when the user asks to review code or check code quality.
keywords:
  - review
  - code review
  - 审查
  - 代码审查
allowed_tools:
  - list_files
  - read_file
  - search_text
verification: A written list of issues, each with a file and line reference.
steps:
  - agent: explorer
    description: Read the code to review (the changed files or the target file).
  - agent: explorer
    description: Analyze the code and report concrete issues with file and line references.
---

Review the code and report: correctness issues, style and readability problems,
potential bugs, and test-coverage gaps. Reference files and lines, and do not
modify any files.
