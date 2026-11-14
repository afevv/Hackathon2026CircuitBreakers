from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import httpx

DICT_PATH = Path(__file__).resolve().parents[1] / "data" / "dynamic_dictionary.json"


def _default_rule_for(item: Dict[str, Any]) -> Dict[str, Any]:
    kind = item["type"]
    if kind == "text":
        return {
            "type": "text",
            "match": item["match"],
            "output": "<FC ID=\"FC_%s\" Source=\"TEXTDATA.TDS\" Legacy=\"%s\" Pattern=\"%1$d\" />" % (item["id"], item["match"]),
            "can_signal": item.get("can_signal") or "GENERIC_TEXT",
            "polarity": item.get("polarity", "NORMAL"),
            "source": "mock_llm",
        }
    return {
        "type": "image",
        "match": item["match"],
        "output": "<Image ID=\"IMG_%s\" Source=\"IMAGEDATA.IDS\" File=\"%s\" />" % (item["id"], item["match"]),
        "can_signal": item.get("can_signal"),
        "polarity": item.get("polarity", "NORMAL"),
        "source": "mock_llm",
    }


def _resolve_dictionary() -> Dict[str, Any]:
    if not DICT_PATH.exists():
        DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DICT_PATH.write_text(json.dumps({"rules": {}, "history": []}, indent=2), encoding="utf-8")
    return json.loads(DICT_PATH.read_text(encoding="utf-8"))


def save_dictionary(payload: Dict[str, Any]) -> None:
    DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DICT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def check_local_ollama() -> bool:
    try:
        response = httpx.get("http://localhost:11434", timeout=0.5)
        return response.status_code == 200
    except Exception:
        return False


def analyze_unknown(item: Dict[str, Any], use_local_ollama: bool = False) -> Dict[str, Any]:
    if use_local_ollama and check_local_ollama():
        return {
            "type": item["type"],
            "match": item["match"],
            "source": "ollama",
            "can_signal": item.get("can_signal") or "AUTO_LEARNED",
            "polarity": item.get("polarity", "NORMAL"),
            "output": "<GeneratedRule Source=\"LOCAL_OLLAMA\" />",
        }

    if item["type"] == "text":
        legacy = item["match"]
        if "Hydraulic Temp" in legacy or "%+04d" in legacy:
            can_signal = "HYDRAULIC_TEMP"
        elif "Supply" in legacy:
            can_signal = "SUPPLY_VOLTAGE"
        else:
            can_signal = "LEGACY_TEXT"
        return {
            "type": "text",
            "match": legacy,
            "can_signal": can_signal,
            "polarity": item.get("polarity", "NORMAL"),
            "source": "mock_llm",
            "output": f"<FC ID=\"FC_{item['id']}\" Source=\"TEXTDATA.TDS\" Legacy=\"{legacy}\" Pattern=\"%1$d\" Signal=\"{can_signal}\" />",
        }

    if "GAUGE" in item["match"] or "Gauge" in item["match"]:
        return {
            "type": "image",
            "match": item["match"],
            "can_signal": item.get("can_signal") or "GAUGE_SIGNAL",
            "polarity": item.get("polarity", "NORMAL"),
            "source": "mock_llm",
            "output": f"<Image ID=\"IMG_{item['id']}\" Source=\"IMAGEDATA.IDS\" File=\"{item['match']}\" LockAspect=\"true\" />",
        }

    return _default_rule_for(item)


def learn_rule(item: Dict[str, Any], use_local_ollama: bool = False) -> Dict[str, Any]:
    rule = analyze_unknown(item, use_local_ollama=use_local_ollama)
    dictionary = _resolve_dictionary()
    rule_id = f"{item['type']}_{item['id']}"
    dictionary.setdefault("rules", {})[rule_id] = rule
    dictionary.setdefault("history", []).append({
        "rule_id": rule_id,
        "match": item["match"],
        "source": rule.get("source", "mock_llm"),
        "learned_at": "auto",
    })
    save_dictionary(dictionary)
    return rule
