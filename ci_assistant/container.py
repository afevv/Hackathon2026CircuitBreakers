"""Composition root: the only place that knows which concrete services are used.

Swap implementations here (e.g. KeywordRetriever -> VectorRetriever) without
touching ViewModels or Views.
"""
from __future__ import annotations

from dataclasses import dataclass

from ci_assistant.config import Settings
from ci_assistant.services.comparison_service import ComparisonEngine
from ci_assistant.services.interfaces import (
    ComparisonEngineProtocol,
    DocumentRetriever,
    LLMClient,
    ProductMatcher,
    ProductRepository,
)
from ci_assistant.services.llm_client import build_llm_client
from ci_assistant.services.matching_service import AttributeDistanceMatcher
from ci_assistant.services.product_repository import JsonProductRepository
from ci_assistant.services.retrieval_service import KeywordRetriever, load_text_documents
from ci_assistant.viewmodels.chat_viewmodel import ChatViewModel
from ci_assistant.viewmodels.comparison_viewmodel import ComparisonViewModel


@dataclass
class Container:
    settings: Settings
    repository: ProductRepository
    retriever: DocumentRetriever
    matcher: ProductMatcher
    engine: ComparisonEngineProtocol
    llm: LLMClient

    # ViewModel factories: views call these, they never construct ViewModels directly.
    def comparison_vm(self) -> ComparisonViewModel:
        return ComparisonViewModel(self.repository, self.matcher, self.engine, top_k=self.settings.top_k)

    def chat_vm(self) -> ChatViewModel:
        return ChatViewModel(self.retriever, self.llm, top_k=self.settings.top_k)


def build_container(settings: Settings) -> Container:
    retriever = KeywordRetriever()
    retriever.index(load_text_documents(settings.docs_dir))
    return Container(
        settings=settings,
        repository=JsonProductRepository(settings.products_file),
        retriever=retriever,
        matcher=AttributeDistanceMatcher(),
        engine=ComparisonEngine(),
        llm=build_llm_client(settings),
    )
