from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.etree import ElementTree as ET

from app.gate_scorer import has_safety_veto, validate_line, validate_migration
from app.generator import generate_srd_xml
from app.image_worker import upscale_assets
from app.llm_worker import learn_rule
from app.models import LineFlag, MigrationResult, UIRDocument
from app.vision_worker import infer_screen_layout

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
            "line_id": item.line_id,
            "match": item.legacy_format,
            "source_file": item.source_file,
            "description": item.description,
            "x": item.x,
            "y": item.y,
            "width": item.width,
            "height": item.height,
            "ocr_text": item.ocr_text,
            "can_signal": item.can_signal,
            "polarity": item.polarity,
        }
    return {
        "type": "image",
        "id": item.id,
        "line_id": item.line_id,
        "match": item.file_name,
        "source_file": item.source_file,
        "description": item.file_name,
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
    screen_image_base64: str | None = None,
    asset_images: Dict[str, str] | None = None,
) -> MigrationResult:
    aspect_ratio_overrides = aspect_ratio_overrides or {}
    asset_images = asset_images or {}
    for index, entry in enumerate(document.text_entries + document.image_entries, start=1):
        if not entry.line_id:
            entry.line_id = f"Line_{index:03d}"
    dictionary = _load_dictionary()
    items: List[Dict[str, Any]] = []
    entries_by_line: Dict[str, Any] = {}
    for text in document.text_entries:
        item = _normalize_item(text)
        items.append(item)
        entries_by_line[item["line_id"]] = text
    for image in document.image_entries:
        item = _normalize_item(image)
        items.append(item)
        entries_by_line[item["line_id"]] = image
    items_by_line = {item["line_id"]: item for item in items}

    logs: List[str] = [f"Stage 1: Indexed {len(items)} resources by legacy line ID."]
    if screen_image_base64:
        inferred_geometry = infer_screen_layout(screen_image_base64, items, asset_images)
        for item in items:
            geometry = inferred_geometry.get(item["line_id"])
            if geometry is not None:
                entry = entries_by_line[item["line_id"]]
                for key, value in geometry.items():
                    if key == "ocr_text" and item["type"] == "text":
                        entry.ocr_text = value
                    elif key != "ocr_text":
                        setattr(entry, key, value)
                    item[key] = value
        logs.append(f"Stage 2: Ollama Vision/OCR matched {len(inferred_geometry)} screen elements.")
    else:
        logs.append("Stage 2: No screenshot supplied; retaining only source-file geometry.")

    unresolved = [item for item in items if _get_rule_for_item(item, dictionary) is None]
    for item in items:
        if _get_rule_for_item(item, dictionary) is not None:
            logs.append(f"Stage 3: {item['line_id']} uses a deterministic dictionary rule.")
    for item in unresolved:
        if has_safety_veto([item]):
            logs.append(f"Stage 3: {item['line_id']} blocked from fallback by the safety veto.")
            continue
        learned = learn_rule(item, use_local_ollama=use_local_ollama)
        logs.append(
            f"Stage 3: {item['line_id']} ({item['match']}) learned via "
            f"{learned.get('source', 'mock_llm')}."
        )

    dictionary = _load_dictionary()
    rules_by_line: Dict[str, Tuple[str, Dict[str, Any]] | None] = {
        item["line_id"]: _find_rule_for_item(item, dictionary) for item in items
    }
    unresolved = [item for item in items if rules_by_line[item["line_id"]] is None]
    applied_rules = []
    for item in items:
        found = rules_by_line[item["line_id"]]
        if found is not None:
            rule_id, rule = found
            item["rule_id"] = rule_id
            applied_rules.append({**rule, "line_id": item["line_id"], "rule_id": rule_id})
    final_xml = generate_srd_xml(document, applied_rules, aspect_ratio_overrides)
    root = ET.fromstring(final_xml)
    library = root.find("ScreenLibrary")
    output_nodes = list(library) if library is not None else []
    nodes_by_line = {node.get("LineID"): node for node in output_nodes}
    upscaled_assets, upscaled_files = upscale_assets(document, nodes_by_line, asset_images)
    logs.append(f"Stage 2: Upscaled {len(upscaled_files)} bitmap assets for the target display.")
    for line_id, filename in upscaled_files.items():
        node = nodes_by_line[line_id]
        item = items_by_line[line_id]
        node.set("LegacyFile", item["match"])
        node.set("File", filename)
    safety_veto = has_safety_veto(items)
    line_flags: List[LineFlag] = []
    translation_trace: List[Dict[str, Any]] = []
    for item in items:
        found = rules_by_line[item["line_id"]]
        rule_id, rule = found if found else ("", {})
        source = rule.get("source")
        if source in {"mock_llm", "ollama"}:
            route = f"Case A · {source} fallback"
        elif found:
            route = "Case B · deterministic dictionary"
        else:
            route = "Blocked · no matching rule"
        generated_node = nodes_by_line.get(item["line_id"])
        node_checks = validate_line(
            item,
            generated_node,
            found is not None,
            asset_available=item["type"] != "image" or item["line_id"] in upscaled_files,
        )
        valid = all(node_checks.values())
        if generated_node is not None:
            generated_node.set("Validation", str(valid).lower())
            generated_node.set("LineFlag", "PASS" if valid else "REVIEW")
        line_flags.append(LineFlag(
            line_id=item["line_id"],
            resource_id=item["id"],
            resource_type=item["type"],
            source_file=item["source_file"],
            legacy_match=item["match"],
            can_signal=item.get("can_signal"),
            polarity=item.get("polarity", "NORMAL"),
            route=route,
            rule_id=rule_id or None,
            valid=valid,
            checks=node_checks,
            flag="PASS" if valid else "REVIEW",
        ))
        translation_trace.append({
            "line_id": item["line_id"],
            "input": item,
            "route": route if valid else "Human review",
            "rule_id": rule_id or None,
            "rule_output": rule.get("output"),
            "generated_srd": ET.tostring(generated_node, encoding="unicode") if generated_node is not None else None,
        })

    final_xml = ET.tostring(root, encoding="unicode")
    validation = (
        bool(line_flags)
        and all(line_flag.valid for line_flag in line_flags)
        and validate_migration(items, final_xml, len(unresolved))
    )
    final_dictionary = _load_dictionary()
    audit_report = json.dumps({
        "validation": validation,
        "lines": [line_flag.model_dump() for line_flag in line_flags],
    }, indent=2)
    logs.extend([
        "Stage 4: Boolean Math Gate checked syntax, safety, layout, and traceability per line.",
        "Stage 5: Invalid lines are flagged for human review; no LLM reassessment loop is run.",
        "Stage 6: Assembled SCREENREPO.SRD and the line-flag audit report.",
    ])
    result = MigrationResult(
        scenario=scenario,
        validation=validation,
        srd_xml=final_xml,
        dictionary_snapshot=final_dictionary,
        aspect_ratio_overrides={
            image_id: {"width": ratio[0], "height": ratio[1]}
            for image_id, ratio in aspect_ratio_overrides.items()
        },
        logs=logs,
        translation_trace=translation_trace,
        line_flags=line_flags,
        line_flag_audit_report=audit_report,
        upscaled_assets=upscaled_assets,
        safety_veto=safety_veto,
        unresolved=len(unresolved),
    )
    return result
