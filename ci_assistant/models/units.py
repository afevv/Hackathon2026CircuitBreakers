"""Unit conversion into the canonical units defined in attribute_schema."""
from __future__ import annotations

_FACTORS: dict[tuple[str, str], float] = {
    ("gpm", "l/min"): 3.785411784,
    ("psi", "bar"): 0.0689475729,
    ("mpa", "bar"): 10.0,
    ("in3/rev", "cc/rev"): 16.387064,
    ("cm3/rev", "cc/rev"): 1.0,
    ("lb", "kg"): 0.45359237,
}


def to_canonical(value: float, unit: str | None, target_unit: str | None) -> float:
    if unit is None or target_unit is None or unit.lower() == target_unit.lower():
        return value
    factor = _FACTORS.get((unit.lower(), target_unit.lower()))
    if factor is None:
        raise ValueError(f"No conversion from {unit!r} to {target_unit!r}")
    return value * factor
