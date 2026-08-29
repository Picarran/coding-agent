"""Retrieval memory (V2-7): recall past task conclusions without a vector DB.

Each completed task is indexed as a ``MemoryEntry`` (task + summary + extracted
keywords + referenced files + timestamp). A new task is scored against the index
by keyword overlap (Jaccard) and the top-K relevant snippets are injected into
the new task's context, so the agent can reuse earlier findings instead of
re-exploring.

This is deliberately keyword-based (no embeddings): it is deterministic, cheap,
and easy to explain — the "小规模可解释" step before a real vector index.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.core.models import AgentResult

_FILE_REF_RE = re.compile(r"[\w./\\-]+\.(?:py|json|txt|md|js|ts|yml|yaml|toml|ini|cfg)")

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "for", "with", "on", "it", "this", "that", "we", "you", "i", "be", "as",
    "at", "by", "if", "not", "so", "do", "does", "but", "from", "me", "my",
    "your", "our", "their", "what", "which", "how", "all", "any", "can",
})


def extract_keywords(text: str, limit: int = 24) -> list[str]:
    """Cheap keyword extraction: English words (minus stopwords) + CJK bigrams."""
    text = (text or "").lower()
    tokens: list[str] = [
        t for t in re.findall(r"[a-z_][a-z0-9_]{1,}", text) if t not in _STOPWORDS
    ]
    # CJK bigrams capture multi-character terms without a segmenter.
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            out.append(token)
        if len(out) >= limit:
            break
    return out


def _extract_files(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _FILE_REF_RE.findall(text or ""):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


@dataclass
class MemoryEntry:
    task_id: str
    task: str
    summary: str
    keywords: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        return f"{self.task}\n{self.summary}"


class RetrievalMemory:
    """Keyword-indexed memory of past task conclusions."""

    def __init__(self, entries: list[MemoryEntry] | None = None) -> None:
        self._entries: list[MemoryEntry] = list(entries or [])

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, task: str, result: AgentResult) -> MemoryEntry:
        text = f"{task}\n{result.summary}"
        entry = MemoryEntry(
            task_id=uuid.uuid4().hex[:8],
            task=task,
            summary=result.summary,
            keywords=extract_keywords(text),
            files=_extract_files(text),
        )
        self._entries.append(entry)
        return entry

    def query(self, task: str, top_k: int = 3) -> list[MemoryEntry]:
        q = set(extract_keywords(task))
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._entries:
            s = self._score(q, entry)
            if s > 0.0:
                scored.append((s, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    @staticmethod
    def _score(query_keywords: set[str], entry: MemoryEntry) -> float:
        e = set(entry.keywords)
        if not query_keywords or not e:
            return 0.0
        return len(query_keywords & e) / len(query_keywords | e)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(e) for e in self._entries]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RetrievalMemory":
        p = Path(path)
        if not p.is_file():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls([MemoryEntry(**d) for d in data])
