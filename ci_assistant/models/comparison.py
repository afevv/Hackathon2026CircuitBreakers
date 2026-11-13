from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ci_assistant.models.product import Product
from ci_assistant.models.source import SourceRef


class Verdict(str, Enum):
    MATCH = "match"
    DIFFERENT = "different"
    MISSING = "missing"  # one or both sides have no value
    CONFLICT = "conflict"  # sources disagree (not yet implemented, see comparison_service)


@dataclass(frozen=True)
class MatchCandidate:
    product: Product
    score: float  # 0..1
    shared_attributes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttributeComparison:
    key: str
    label: str
    unit: str | None
    left_value: float | str | None
    right_value: float | str | None
    verdict: Verdict
    delta_pct: float | None = None
    left_source: SourceRef | None = None
    right_source: SourceRef | None = None
    note: str = ""


@dataclass(frozen=True)
class ComparisonResult:
    left: Product
    right: Product
    rows: tuple[AttributeComparison, ...]
    match_score: float
    uncertainties: tuple[str, ...] = field(default_factory=tuple)
