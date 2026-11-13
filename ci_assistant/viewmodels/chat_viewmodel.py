from __future__ import annotations

from dataclasses import dataclass, field

from ci_assistant.models import SourceRef
from ci_assistant.services.interfaces import DocumentRetriever, LLMClient
from ci_assistant.viewmodels.base import ObservableViewModel

SYSTEM_PROMPT = (
    "You are a competitor-intelligence assistant for B2B sales. Answer ONLY from the numbered "
    "sources provided. Cite sources as [1], [2]. If sources are missing or conflict, say so "
    "explicitly. The user makes the final commercial and technical decisions."
)


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    text: str
    citations: tuple[SourceRef, ...] = field(default_factory=tuple)


class ChatViewModel(ObservableViewModel):
    """Natural-language Q&A grounded in retrieved passages (RAG), with citations."""

    def __init__(self, retriever: DocumentRetriever, llm: LLMClient, top_k: int = 5):
        super().__init__()
        self._retriever, self._llm, self._top_k = retriever, llm, top_k
        self._messages: list[ChatMessage] = []

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def ask(self, question: str) -> None:
        question = question.strip()
        if not question:
            return
        self._messages.append(ChatMessage("user", question))
        passages = self._retriever.search(question, self._top_k)
        if not passages:
            answer = "I found no matching sources, so I can't answer that reliably."
            self._messages.append(ChatMessage("assistant", answer))
        else:
            context = "\n\n".join(f"[{i}] ({p.source.short()})\n{p.text}" for i, p in enumerate(passages, 1))
            answer = self._llm.complete(SYSTEM_PROMPT, f"Sources:\n{context}\n\nQuestion: {question}")
            self._messages.append(ChatMessage("assistant", answer, tuple(p.source for p in passages)))
        for name in ("messages",):
            for listener in list(self._listeners):
                listener(name)

    def clear(self) -> None:
        self._messages.clear()
        for listener in list(self._listeners):
            listener("messages")
