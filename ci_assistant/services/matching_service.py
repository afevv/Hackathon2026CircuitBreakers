from __future__ import annotations

from typing import Sequence

from ci_assistant.models import CANONICAL_ATTRIBUTES, MatchCandidate, Product


class AttributeDistanceMatcher:
    """Ranks candidates by weighted similarity over shared canonical attributes.

    Score is multiplied by coverage (share of total schema weight that both
    products actually have), so sparse data can't outrank well-documented matches.

    TODO: add family filtering, model-code parsing, and alternative matches/gaps.
    """

    def __init__(self, schema=CANONICAL_ATTRIBUTES):
        self.schema = schema

    def rank(self, target: Product, candidates: Sequence[Product], top_k: int = 5) -> list[MatchCandidate]:
        total_weight = sum(s.weight for s in self.schema.values())
        ranked: list[MatchCandidate] = []
        for cand in candidates:
            if cand.id == target.id:
                continue
            weighted_sim = used_weight = 0.0
            shared: list[str] = []
            for spec in self.schema.values():
                a, b = target.get(spec.key), cand.get(spec.key)
                if a is None or b is None:
                    continue
                shared.append(spec.key)
                used_weight += spec.weight
                weighted_sim += spec.weight * self._similarity(spec.kind, a.value, b.value)
            if not shared:
                continue
            score = (weighted_sim / used_weight) * (used_weight / total_weight)
            ranked.append(MatchCandidate(product=cand, score=round(score, 4), shared_attributes=tuple(shared)))
        ranked.sort(key=lambda m: m.score, reverse=True)
        return ranked[:top_k]

    @staticmethod
    def _similarity(kind: str, a, b) -> float:
        if kind == "text":
            return 1.0 if str(a).strip().casefold() == str(b).strip().casefold() else 0.0
        a, b = float(a), float(b)
        denom = max(abs(a), abs(b))
        return 1.0 if denom == 0 else max(0.0, 1.0 - abs(a - b) / denom)
