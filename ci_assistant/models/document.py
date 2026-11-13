from __future__ import annotations

from dataclasses import dataclass

from ci_assistant.models.source import SourceRef


@dataclass(frozen=True)
class Document:
    id: str
    text: str
    source: SourceRef


@dataclass(frozen=True)
class Passage:
    """A retrieved chunk of a document, with a relevance score."""

    text: str
    source: SourceRef
    score: float
