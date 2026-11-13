from __future__ import annotations

import pandas as pd

from ci_assistant.models import ComparisonResult, MatchCandidate, Product
from ci_assistant.services.interfaces import ComparisonEngineProtocol, ProductMatcher, ProductRepository
from ci_assistant.viewmodels.base import ObservableViewModel


class ComparisonViewModel(ObservableViewModel):
    """State and commands for: pick a Danfoss product -> see matches -> compare."""

    def __init__(self, repo: ProductRepository, matcher: ProductMatcher,
                 engine: ComparisonEngineProtocol, top_k: int = 5):
        super().__init__()
        self._repo, self._matcher, self._engine, self._top_k = repo, matcher, engine, top_k
        self._targets: list[Product] = []
        self._target: Product | None = None
        self._candidates: list[MatchCandidate] = []
        self._result: ComparisonResult | None = None
        self._error: str | None = None

    # ---- state (read-only for the View) ----
    @property
    def targets(self) -> list[Product]:
        return self._targets

    @property
    def target(self) -> Product | None:
        return self._target

    @property
    def candidates(self) -> list[MatchCandidate]:
        return self._candidates

    @property
    def result(self) -> ComparisonResult | None:
        return self._result

    @property
    def error(self) -> str | None:
        return self._error

    # ---- commands (called by the View) ----
    def load(self) -> None:
        self._set("targets", self._repo.list_danfoss())

    def select_target(self, product_id: str) -> None:
        target = self._repo.get(product_id)
        if target is None:
            self._set("error", f"Unknown product: {product_id}")
            return
        self._set("error", None)
        self._set("target", target)
        self._set("result", None)
        self._set("candidates", self._matcher.rank(target, self._repo.list_competitors(), self._top_k))

    def select_candidate(self, product_id: str) -> None:
        if self._target is None:
            self._set("error", "Select a Danfoss product first.")
            return
        other = self._repo.get(product_id)
        if other is None:
            self._set("error", f"Unknown product: {product_id}")
            return
        self._set("error", None)
        self._set("result", self._engine.compare(self._target, other))

    # ---- view helpers ----
    def candidates_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"id": c.product.id, "product": c.product.label, "score": c.score,
              "shared attributes": len(c.shared_attributes)} for c in self._candidates]
        )

    def comparison_dataframe(self) -> pd.DataFrame:
        if self._result is None:
            return pd.DataFrame()
        r = self._result
        return pd.DataFrame(
            [{
                "Attribute": row.label,
                "Unit": row.unit or "",
                r.left.label: row.left_value,
                r.right.label: row.right_value,
                "Δ %": row.delta_pct,
                "Verdict": row.verdict.value,
                "Source (left)": row.left_source.url if row.left_source else "",
                "Source (right)": row.right_source.url if row.right_source else "",
                "Note": row.note,
            } for row in r.rows]
        )
