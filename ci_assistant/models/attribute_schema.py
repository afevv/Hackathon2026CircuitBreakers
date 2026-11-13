"""The 'common format': canonical attributes every product is described in.

Extend this (e.g. dimensions, model-code fields, price) and the matcher and
comparison engine pick the new attributes up automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AttributeSpec:
    key: str
    label: str
    kind: Literal["numeric", "text"] = "numeric"
    unit: str | None = None  # canonical unit
    rel_tolerance: float = 0.05  # within this relative difference counts as "equal"
    weight: float = 1.0  # importance in matching


CANONICAL_ATTRIBUTES: dict[str, AttributeSpec] = {
    spec.key: spec
    for spec in [
        AttributeSpec("max_flow", "Max flow", unit="l/min", rel_tolerance=0.10, weight=2.0),
        AttributeSpec("max_pressure", "Max pressure", unit="bar", rel_tolerance=0.05, weight=2.0),
        AttributeSpec("displacement", "Displacement", unit="cc/rev", rel_tolerance=0.05),
        AttributeSpec("work_sections", "Work sections", unit=None, rel_tolerance=0.0, weight=1.5),
        AttributeSpec("mass", "Mass", unit="kg", rel_tolerance=0.15, weight=0.5),
        AttributeSpec("port_interface", "Port interface", kind="text", weight=1.0),
    ]
}
