from __future__ import annotations

from ci_assistant.models import (
    CANONICAL_ATTRIBUTES,
    AttributeComparison,
    ComparisonResult,
    Product,
    Verdict,
)
from ci_assistant.services.matching_service import AttributeDistanceMatcher


class ComparisonEngine:
    """Builds a side-by-side, source-linked comparison of two products."""

    def __init__(self, schema=CANONICAL_ATTRIBUTES):
        self.schema = schema

    def compare(self, left: Product, right: Product) -> ComparisonResult:
        rows: list[AttributeComparison] = []
        uncertainties: list[str] = []

        for spec in self.schema.values():
            a, b = left.get(spec.key), right.get(spec.key)

            if a is None or b is None:
                missing = [p.label for p, v in ((left, a), (right, b)) if v is None]
                rows.append(
                    AttributeComparison(
                        key=spec.key, label=spec.label, unit=spec.unit,
                        left_value=a.value if a else None, right_value=b.value if b else None,
                        verdict=Verdict.MISSING,
                        left_source=a.source if a else None, right_source=b.source if b else None,
                        note=f"No data for: {', '.join(missing)}",
                    )
                )
                uncertainties.append(f"{spec.label}: missing for {', '.join(missing)}")
                continue

            delta_pct, same = None, False
            if spec.kind == "numeric":
                x, y = float(a.value), float(b.value)
                denom = max(abs(x), abs(y))
                rel = 0.0 if denom == 0 else abs(x - y) / denom
                delta_pct = round(100 * (y - x) / x, 1) if x else None
                same = rel <= spec.rel_tolerance
            else:
                same = str(a.value).strip().casefold() == str(b.value).strip().casefold()

            for p, v in ((left, a), (right, b)):
                if v.source is None:
                    uncertainties.append(f"{spec.label}: no source reference for {p.label}")

            rows.append(
                AttributeComparison(
                    key=spec.key, label=spec.label, unit=spec.unit,
                    left_value=a.value, right_value=b.value,
                    verdict=Verdict.MATCH if same else Verdict.DIFFERENT,
                    delta_pct=delta_pct, left_source=a.source, right_source=b.source,
                )
            )

        # TODO: Verdict.CONFLICT: when the same product has differing values for one
        # attribute across several sources, flag it here instead of picking one.

        score = AttributeDistanceMatcher(self.schema).rank(left, [right], top_k=1)
        return ComparisonResult(
            left=left, right=right, rows=tuple(rows),
            match_score=score[0].score if score else 0.0,
            uncertainties=tuple(uncertainties),
        )
