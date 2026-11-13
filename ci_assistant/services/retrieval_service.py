from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from ci_assistant.models import Document, Passage, SourceRef

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def load_text_documents(directory: Path) -> list[Document]:
    """Reads .txt/.md files. Extend for PDFs (pypdf) and scraped HTML."""
    docs: list[Document] = []
    directory = Path(directory)
    if not directory.exists():
        return docs
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() in {".txt", ".md"}:
            docs.append(Document(id=path.name, text=path.read_text(encoding="utf-8"),
                                 source=SourceRef(url=path.as_posix(), title=path.name)))
    return docs


class KeywordRetriever:
    """Naive keyword-overlap retriever over paragraph chunks.

    TODO: replace with embeddings + vector store for real RAG. Keep the interface:
    index(documents) and search(query, top_k) -> list[Passage] (with SourceRef).
    """

    def __init__(self) -> None:
        self._chunks: list[tuple[str, SourceRef, Counter]] = []

    def index(self, documents: Sequence[Document]) -> None:
        self._chunks.clear()
        for doc in documents:
            for chunk in (c.strip() for c in re.split(r"\n\s*\n", doc.text) if c.strip()):
                self._chunks.append((chunk, doc.source, Counter(_tokens(chunk))))

    def search(self, query: str, top_k: int = 5) -> list[Passage]:
        q = set(_tokens(query))
        scored = []
        for text, source, counts in self._chunks:
            hits = sum(counts[t] for t in q)
            if hits:
                scored.append(Passage(text=text, source=source, score=float(hits)))
        scored.sort(key=lambda p: p.score, reverse=True)
        return scored[:top_k]
