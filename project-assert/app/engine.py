from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.etree import ElementTree as ET

from app.gate_scorer import score_migration
from app.generator import generate_srd_xml
from app.llm_worker import learn_rule
from app.models import MigrationResult, UIRDocument

DICT_PATH = Path(__file__).resolve().parents[1] / "data" / "dynamic_dictionary.json"


def _load_dictionary() -> Dict[str, Any]:
    if not DICT_PATH.exists():
        DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DICT_PATH.write_text(json.dumps({"rules": {}, "history": []}, indent=2), encoding="utf-8")
    return json.loads(DICT_PATH.read_text(encoding="utf-8"))


def _find_rule_for_item(item: Dict[str, Any], dictionary: Dict[str, Any]) -> Tuple[str, Dict[str, Any]] | None:
    rules = dictionary.get("rules", {})
    candidates = [
        item.get("legacy_format") or item.get("match"),
        item.get("file_name") or item.get("match"),
        item.get("id"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        for rule_id, rule in rules.items():
            if isinstance(rule, dict):
                match_value = rule.get("match") or rule.get("legacy_pattern") or rule.get("file_name") or ""
                if candidate == match_value:
                    return rule_id, rule
    return None


def _get_rule_for_item(item: Dict[str, Any], dictionary: Dict[str, Any]) -> Dict[str, Any] | None:
    found = _find_rule_for_item(item, dictionary)
    return found[1] if found else None


def _normalize_item(item: Any) -> Dict[str, Any]:
    if hasattr(item, "legacy_format"):
        return {
            "type": "text",
            "id": item.id,
            "match": item.legacy_format,
            "can_signal": item.can_signal,
            "polarity": item.polarity,
        }
    return {
        "type": "image",
        "id": item.id,
        "match": item.file_name,
        "can_signal": item.can_signal,
        "polarity": item.polarity,
        "x": item.x,
        "y": item.y,
        "width": item.width,
        "height": item.height,
    }


def run_migration(
    document: UIRDocument,
    scenario: str = "demo",
    use_local_ollama: bool = False,
    aspect_ratio_overrides: Dict[str, Tuple[int, int]] | None = None,
) -> MigrationResult:
    aspect_ratio_overrides = aspect_ratio_overrides or {}
    dictionary = _load_dictionary()
    logs: List[str] = []
    unresolved: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    for text in document.text_entries:
        item = _normalize_item(text)
        items.append(item)
        if _get_rule_for_item(item, dictionary) is None:
            unresolved.append(item)
    for image in document.image_entries:
        item = _normalize_item(image)
        items.append(item)
        if _get_rule_for_item(item, dictionary) is None:
            unresolved.append(item)

    iteration = 0
    while True:
        if unresolved:
            logs.append(f"Iteration {iteration}: {len(unresolved)} unresolved rules detected.")
            for item in unresolved:
                if item["type"] == "text" and "Hydraulic Temp" in (item.get("match") or ""):
                    learned = learn_rule(item, use_local_ollama=use_local_ollama)
                    logs.append(f"Self-healing: learned {item['match']} via {learned.get('source','mock_llm')}.")
                elif item["type"] == "image" and "GAUGE" in (item.get("match") or "").upper():
                    learned = learn_rule(item, use_local_ollama=use_local_ollama)
                    logs.append(f"Self-healing: learned {item['match']} via {learned.get('source','mock_llm')}.")
                else:
                    if item.get("can_signal") == "CAN_HYD_FAULT" and item.get("polarity") == "INVERTED":
                        pass
                    else:
                        learned = learn_rule(item, use_local_ollama=use_local_ollama)
                        logs.append(f"Self-healing: learned {item['match']} via {learned.get('source','mock_llm')}.")
        dictionary = _load_dictionary()
        unresolved = []
        for item in items:
            if _get_rule_for_item(item, dictionary) is None:
                unresolved.append(item)

        srd_xml = generate_srd_xml(document, [], aspect_ratio_overrides)
        metrics = score_migration(items, srd_xml, len(unresolved), scenario=scenario)
        logs.append(f"Iteration {iteration}: score {metrics.total}, tier {'Green' if metrics.total >= 95 else 'Amber' if metrics.total >= 70 else 'Red'}.")
        if metrics.total >= 95 or metrics.safety_veto or metrics.total < 70:
            break
        if unresolved:
            iteration += 1
            continue
        break

    if scenario == "hazard":
        for item in items:
            if item.get("can_signal") == "CAN_HYD_FAULT" and item.get("polarity") == "INVERTED":
                metrics = score_migration(items, generate_srd_xml(document, [], aspect_ratio_overrides), 0, scenario=scenario)
                metrics.safety_veto = True
                metrics.total = min(metrics.total, 68.0)
                break

    final_xml = generate_srd_xml(document, [], aspect_ratio_overrides)
    library = ET.fromstring(final_xml).find("ScreenLibrary")
    output_nodes = list(library) if library is not None else []
    final_dictionary = _load_dictionary()
    translation_trace: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        found = _find_rule_for_item(item, final_dictionary)
        rule_id, rule = found if found else ("", {})
        source = rule.get("source")
        if source in {"mock_llm", "ollama"}:
            route = f"Case A · {source}"
        elif found:
            route = "Case B · dictionary"
        else:
            route = "Blocked · no matching rule"
        generated_node = output_nodes[index] if index < len(output_nodes) else None
        translation_trace.append({
            "input": item,
            "route": route,
            "rule_id": rule_id or None,
            "rule_output": rule.get("output"),
            "generated_srd": ET.tostring(generated_node, encoding="unicode") if generated_node is not None else None,
        })

    result = MigrationResult(
        scenario=scenario,
        tier="Green" if metrics.total >= 95 else "Amber" if metrics.total >= 70 else "Red",
        iterations=iteration + 1,
        score=metrics.total,
        srd_xml=final_xml,
        dictionary_snapshot=final_dictionary,
        aspect_ratio_overrides={
            image_id: {"width": ratio[0], "height": ratio[1]}
            for image_id, ratio in aspect_ratio_overrides.items()
        },
        logs=logs,
        translation_trace=translation_trace,
        safety_veto=metrics.safety_veto,
        unresolved=metrics.unresolved,
    )
    return result
