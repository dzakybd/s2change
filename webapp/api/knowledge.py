"""The analyst's background notes (webapp/api/knowledge/*.md), indexed by the topic table in 00_index.md."""

from __future__ import annotations

import re
from pathlib import Path

DIR = Path(__file__).parent / "knowledge"
_ROW = re.compile(r"^\|\s*`(?P<topic>\w+)`\s*\|\s*(?P<file>[\w.-]+\.md)\s*\|", re.MULTILINE)

FILES: dict[str, str] = {m["topic"]: m["file"] for m in _ROW.finditer((DIR / "00_index.md").read_text())}
TOPICS = tuple(FILES)


def read(topic: str) -> str:
    """The markdown of one topic. Raises ValueError for an unknown topic."""
    if topic not in FILES:
        raise ValueError(f"Unknown topic {topic!r}. Topics: {', '.join(TOPICS)}.")
    return (DIR / FILES[topic]).read_text()
