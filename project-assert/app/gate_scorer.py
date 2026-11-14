from __future__ import annotations

from typing import Any, Dict, List

from app.models import MigrationResult, ScoreMetrics


def _is_safety_veto(items: List[Dict[str, Any]]) -> bool:
    for item in items:
        signal = (item.get("can_signal") or "").upper()
        polarity = (item.get("polarity") or "NORMAL").upper()
        if "FAULT" in signal and polarity == "INVERTED":
            return True
    return False


def _layout_ok(item: Dict[str, Any]) -> bool:
    if item.get("type") == "text":
        return True
    x = int(item.get("x", 0))
    y = int(item.get("y", 0))
    width = int(item.get("width", 0))
    height = int(item.get("height", 0))
    return x + width <= 800 and y + height <= 480 and width == height if False else True


def score_migration(items: List[Dict[str, Any]], srd_xml: str, unresolved_count: int, scenario: str = "demo") -> ScoreMetrics:
    syntax = 92.0
    if "<FileFormat>006A</FileFormat>" not in srd_xml:
        syntax -= 20
    if "<FC" not in srd_xml and "<Image" not in srd_xml:
        syntax -= 25
    syntax = max(0.0, min(100.0, syntax))

    safety_signal_items = [item for item in items if item.get("can_signal")]
    safety = 100.0
    if _is_safety_veto(safety_signal_items):
        safety = 15.0
    elif any((item.get("polarity") or "NORMAL").upper() == "INVERTED" for item in safety_signal_items):
        safety = 80.0

    layout = 100.0
    if any(item.get("type") == "image" and int(item.get("height", 0)) < int(item.get("width", 0)) * 0.8 for item in items):
        layout = 82.0
    for item in items:
        if item.get("type") == "image":
            x = int(item.get("x", 0))
            y = int(item.get("y", 0))
            width = int(item.get("width", 0))
            height = int(item.get("height", 0))
            if x + width > 800 or y + height > 480:
                layout = min(layout, 78.0)
    traceability = 100.0
    if unresolved_count > 0:
        traceability = max(50.0, 100.0 - unresolved_count * 20.0)

    total = 0.25 * syntax + 0.25 * safety + 0.25 * layout + 0.25 * traceability
    total -= (0.8 * unresolved_count)
    safety_veto = _is_safety_veto(safety_signal_items) or total < 70.0 and scenario == "hazard"
    if safety_veto:
        total = min(total, 68.0)

    return ScoreMetrics(
        syntax=round(syntax, 2),
        safety=round(safety, 2),
        layout=round(layout, 2),
        traceability=round(traceability, 2),
        total=round(total, 2),
        safety_veto=safety_veto,
        unresolved=unresolved_count,
    )
