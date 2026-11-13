"""Service contracts. ViewModels depend on these Protocols, never on concrete classes."""
from __future__ import annotations

from typing import Protocol, Sequence

from ci_assistant.models import ComparisonResult, Document, MatchCandidate, Passage, Product
from ci_assistant.models.product import AttributeValue


class ProductRepository(Protocol):
    def list_all(self) -> list[Product]: ...
    def list_danfoss(self) -> list[Product]: ...
    def list_competitors(self) -> list[Product]: ...
    def get(self, product_id: str) -> Product | None: ...


class DocumentRetriever(Protocol):
    def index(self, documents: Sequence[Document]) -> None: ...
    def search(self, query: str, top_k: int = 5) -> list[Passage]: ...


class AttributeExtractor(Protocol):
    def extract(self, document: Document) -> dict[str, AttributeValue]: ...


class ProductMatcher(Protocol):
    def rank(self, target: Product, candidates: Sequence[Product], top_k: int = 5) -> list[MatchCandidate]: ...


class ComparisonEngineProtocol(Protocol):
    def compare(self, left: Product, right: Product) -> ComparisonResult: ...


class LLMClient(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...
