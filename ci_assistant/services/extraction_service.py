from __future__ import annotations

import re

from ci_assistant.models import CANONICAL_ATTRIBUTES, AttributeValue, Document
from ci_assistant.models.units import to_canonical

# key -> regex capturing (value, unit). Toy patterns for the template.
_PATTERNS: dict[str, re.Pattern] = {
    "max_flow": re.compile(r"max(?:imum)?\s+flow\D{0,5}([\d.]+)\s*(l/min|gpm)", re.I),
    "max_pressure": re.compile(r"max(?:imum)?\s+pressure\D{0,5}([\d.]+)\s*(bar|psi|mpa)", re.I),
    "displacement": re.compile(r"displacement\D{0,5}([\d.]+)\s*(cc/rev|cm3/rev|in3/rev)", re.I),
}


class RegexAttributeExtractor:
    """Pulls canonical attributes out of free text, keeping the source reference.

    TODO: replace/complement with LLM structured extraction (JSON schema output)
    and PDF table parsing. Keep the return type: {attribute_key: AttributeValue}.
    """

    def extract(self, document: Document) -> dict[str, AttributeValue]:
        found: dict[str, AttributeValue] = {}
        for key, pattern in _PATTERNS.items():
            m = pattern.search(document.text)
            if not m:
                continue
            raw_value, unit = float(m.group(1)), m.group(2)
            spec = CANONICAL_ATTRIBUTES[key]
            found[key] = AttributeValue(
                value=to_canonical(raw_value, unit, spec.unit),
                unit=spec.unit,
                source=document.source,
                raw=m.group(0),
            )
        return found
