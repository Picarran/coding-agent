"""Skills (V2-8): reusable workflow templates, modeled on Claude Code Agent Skills.

A skill is a folder containing a ``SKILL.md`` file with YAML frontmatter
(``name``, ``description``, ``keywords``, ``allowed_tools``, ``verification``,
``steps``) plus a markdown body of guidance.

The Claude Code idea we adopt is **progressive disclosure**: the Planner only
ever sees the *catalog* (name + description) of available skills; the full body
and steps are loaded only when a skill is matched — so unused skills cost no
context.

We add two adaptations for a deterministic plan-and-execute runtime:

- ``keywords`` — a cheap, deterministic match (no extra LLM call), testable.
- ``steps`` — a concrete step template that the Main Agent executes directly,
  skipping the LLM Planner when a skill matches (more stable than free planning).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillStep:
    agent: str
    description: str


@dataclass
class Skill:
    name: str
    description: str
    steps: list[SkillStep]
    keywords: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    verification: str = ""
    body: str = ""

    def guidance(self) -> str:
        parts: list[str] = []
        if self.body.strip():
            parts.append(self.body.strip())
        if self.verification.strip():
            parts.append("Verification: " + self.verification.strip())
        if self.allowed_tools:
            parts.append("Allowed tools: " + ", ".join(self.allowed_tools))
        return "\n\n".join(parts)


class SkillRegistry:
    """Loads skills from a ``skills/`` directory and exposes a context catalog."""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills: list[Skill] = list(skills or [])

    def all(self) -> list[Skill]:
        return list(self._skills)

    def names(self) -> list[str]:
        return sorted(s.name for s in self._skills)

    def get(self, name: str) -> Skill | None:
        for skill in self._skills:
            if skill.name == name:
                return skill
        return None

    def catalog(self) -> str:
        """Name + description only — the progressive-disclosure metadata."""
        if not self._skills:
            return ""
        lines = ["Available skills (match one if the task fits):"]
        for skill in self._skills:
            lines.append(f"- {skill.name}: {skill.description}")
        return "\n".join(lines)

    @classmethod
    def load(cls, skills_dir: Path | str) -> "SkillRegistry":
        base = Path(skills_dir)
        if not base.is_dir():
            return cls()
        skills: list[Skill] = []
        for skill_md in sorted(base.glob("*/SKILL.md")):
            skill = cls._parse(skill_md)
            if skill is not None:
                skills.append(skill)
            else:
                logger.warning("skipping invalid skill: %s", skill_md)
        return cls(skills)

    @classmethod
    def load_dirs(cls, dirs: list[Path | str]) -> "SkillRegistry":
        """Merge several skill directories; later dirs override earlier by name."""
        merged: dict[str, Skill] = {}
        for d in dirs:
            for skill in cls.load(d).all():
                merged[skill.name] = skill
        return cls(list(merged.values()))

    @staticmethod
    def _parse(path: Path) -> Skill | None:
        text = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(text)
        if meta is None:
            return None
        steps = [
            SkillStep(
                agent=str(s.get("agent") or "coding"),
                description=str(s.get("description") or ""),
            )
            for s in (meta.get("steps") or [])
            if isinstance(s, dict)
        ]
        if not steps:
            return None
        return Skill(
            name=str(meta.get("name") or path.parent.name),
            description=str(meta.get("description") or ""),
            steps=steps,
            keywords=[str(k) for k in (meta.get("keywords") or [])],
            allowed_tools=[str(t) for t in (meta.get("allowed_tools") or [])],
            verification=str(meta.get("verification") or ""),
            body=body.strip(),
        )


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    text = text.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip()
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, body
    return (meta if isinstance(meta, dict) else None), body


class SkillMatcher:
    """Deterministic keyword matcher: first skill whose keyword appears in the task."""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def match(self, task: str) -> Skill | None:
        text = (task or "").lower()
        for skill in self._registry.all():
            for keyword in skill.keywords:
                if keyword.lower() in text:
                    return skill
        return None


def discover_skill_dirs(root: Path | str) -> list[Path]:
    """Layered skill locations, lowest precedence first (later overrides earlier).

    - built-in  ``<project>/skills``
    - project   ``<workspace>/.coding-agent/skills``
    - personal  ``~/.coding-agent/skills``
    """
    builtin = Path(__file__).resolve().parent.parent.parent / "skills"
    return [
        builtin,
        Path(root) / ".coding-agent" / "skills",
        Path.home() / ".coding-agent" / "skills",
    ]
