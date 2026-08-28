"""Workspace context (Layer 2): compact shared state passed between SubAgents.

The Main Agent accumulates key facts from completed steps (inspected files,
modified files, findings, latest test result) and injects a SHORT block into the
next subtask, so SubAgents do not re-read files or re-discover what is already
known. This keeps multi-agent communication efficient and concise.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.core.models import AgentResult


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


@dataclass
class WorkspaceContext:
    inspected_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    test_result: str | None = None

    def record(self, result: AgentResult) -> None:
        report = result.artifacts.get("report") or {}
        for f in report.get("relevant_files") or []:
            self._add(self.inspected_files, str(f))
        for f in report.get("modified_files") or []:
            self._add(self.modified_files, str(f))
            self._add(self.inspected_files, str(f))
        findings = report.get("findings")
        if findings:
            self.findings.append(_clip(str(findings), 400))
        if report.get("command"):
            self.test_result = _clip(result.summary or "", 200)

    def render(self) -> str:
        lines: list[str] = []
        if self.inspected_files:
            lines.append(f"- Files already inspected: {', '.join(self.inspected_files)}")
        if self.modified_files:
            lines.append(f"- Files already modified: {', '.join(self.modified_files)}")
        if self.test_result:
            lines.append(f"- Latest test result: {self.test_result}")
        for finding in self.findings[-3:]:
            lines.append(f"- Finding: {finding}")
        if not lines:
            return ""
        return (
            "## Workspace context (already known; do not redo this work)\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _add(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)
