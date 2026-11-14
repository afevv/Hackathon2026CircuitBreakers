from __future__ import annotations

import re
from typing import Any, Dict, List
from xml.etree import ElementTree as ET


def has_safety_veto(items: List[Dict[str, Any]]) -> bool:
    return any(
        "FAULT" in (item.get("can_signal") or "").upper()
        and (item.get("polarity") or "NORMAL").upper() == "INVERTED"
        for item in items
    )


def _ocr_matches(item: Dict[str, Any]) -> bool:
    actual_text = item.get("ocr_text")
    legacy_text = item.get("match")
    if not isinstance(actual_text, str) or not isinstance(legacy_text, str):
        return False
    parts = re.split(r"%(?:\+)?0?\d*d", legacy_text)
    pattern = ".*".join(re.escape(part.strip()) for part in parts if part.strip())
    return bool(pattern) and re.search(pattern, actual_text, re.IGNORECASE) is not None


def validate_line(
    item: Dict[str, Any],
    node: ET.Element | None,
    rule_found: bool,
    asset_available: bool = True,
) -> Dict[str, bool]:
    is_text = item.get("type") == "text"
    expected_tag = "FC" if is_text else "Image"
    expected_id = f"{'FC' if is_text else 'IMG'}_{item.get('id')}"
    syntax = node is not None and node.tag == expected_tag and node.get("ID") == expected_id
    if syntax and is_text:
        string_node = node.find("String")
        syntax = (
            node.find("Format") is not None
            and string_node is not None
            and string_node.get("TextData") is not None
            and _ocr_matches(item)
        )
        if syntax and string_node is not None:
            has_printf_token = re.search(r"%(?:\+)?0?\d*d", item.get("match", "")) is not None
            if has_printf_token:
                inputs = node.find("Inputs")
                syntax = (
                    "%1$" in (string_node.get("TextData") or "")
                    and inputs is not None
                    and inputs.get("Count") == "1"
                    and inputs.find("FC") is not None
                )
    elif syntax:
        syntax = (node.get("LegacyFile") or node.get("File")) == item.get("match")

    safety = (
        node is not None
        and not has_safety_veto([item])
        and node.get("Signal") == (item.get("can_signal") or ("GENERIC_TEXT" if is_text else "GENERIC_IMAGE"))
        and node.get("Polarity") == (item.get("polarity") or "NORMAL")
    )

    geometry = node.find("Geometry") if node is not None and is_text else node
    try:
        source_x = int(item["x"])
        source_y = int(item["y"])
        source_width = int(item["width"])
        source_height = int(item["height"])
        output_x = int(geometry.get("X", ""))
        output_y = int(geometry.get("Y", ""))
        output_width = int(geometry.get("Width", ""))
        output_height = int(geometry.get("Height", ""))
    except (KeyError, TypeError, ValueError, AttributeError):
        layout = False
    else:
        layout = (
            source_x >= 0
            and source_y >= 0
            and source_width > 0
            and source_height > 0
            and source_x + source_width <= 320
            and source_y + source_height <= 240
            and output_x >= 0
            and output_y >= 0
            and output_width > 0
            and output_height > 0
            and output_x + output_width <= 800
            and output_y + output_height <= 480
        )

    traceability = (
        node is not None
        and bool(item.get("line_id"))
        and node.get("LineID") == item.get("line_id")
        and node.get("Provenance") == f"{item.get('source_file')}|{item.get('id')}"
        and node.get("RuleID") == item.get("rule_id")
        and rule_found
        and (is_text or asset_available)
    )
    return {
        "syntax": syntax,
        "safety": safety,
        "layout": layout,
        "traceability": traceability,
    }


def validate_migration(items: List[Dict[str, Any]], srd_xml: str, unresolved_count: int) -> bool:
    if not items or unresolved_count or has_safety_veto(items):
        return False
    try:
        root = ET.fromstring(srd_xml)
    except ET.ParseError:
        return False
    if root.tag != "ScreenRepository" or root.findtext("FileFormat") != "006A":
        return False
    library = root.find("ScreenLibrary")
    if library is None or len(library) != len(items):
        return False
    nodes = {node.get("LineID"): node for node in library}
    for item in items:
        node = nodes.get(item.get("line_id"))
        checks = validate_line(item, node, rule_found=True)
        if not all(checks.values()):
            return False
    return True
