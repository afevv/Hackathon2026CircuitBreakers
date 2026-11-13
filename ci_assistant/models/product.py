from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ci_assistant.models.attribute_schema import CANONICAL_ATTRIBUTES
from ci_assistant.models.source import SourceRef
from ci_assistant.models.units import to_canonical


@dataclass(frozen=True)
class AttributeValue:
    value: float | str
    unit: str | None = None  # always the canonical unit after normalisation
    source: SourceRef | None = None
    raw: str = ""  # original text/unit before normalisation, for traceability


@dataclass
class Product:
    id: str
    manufacturer: str
    model_code: str
    family: str
    is_danfoss: bool = False
    source: SourceRef | None = None
    attributes: dict[str, AttributeValue] = field(default_factory=dict)

    def get(self, key: str) -> AttributeValue | None:
        return self.attributes.get(key)

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.model_code}"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Product":
        attrs: dict[str, AttributeValue] = {}
        for key, raw in d.get("attributes", {}).items():
            spec = CANONICAL_ATTRIBUTES.get(key)
            if spec is None:
                continue  # unknown attribute: ignore (or log) until added to the schema
            value, unit = raw["value"], raw.get("unit")
            src = SourceRef(url=raw["source_url"]) if raw.get("source_url") else None
            raw_text = f"{value} {unit or ''}".strip()
            if spec.kind == "numeric":
                value = to_canonical(float(value), unit, spec.unit)
                unit = spec.unit
            attrs[key] = AttributeValue(value=value, unit=unit, source=src, raw=raw_text)
        return cls(
            id=d["id"],
            manufacturer=d["manufacturer"],
            model_code=d["model_code"],
            family=d.get("family", ""),
            is_danfoss=d.get("is_danfoss", False),
            source=SourceRef(url=d["source_url"]) if d.get("source_url") else None,
            attributes=attrs,
        )
