from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.engine as engine
import app.llm_worker as llm_worker
from app.parser import parse_legacy_xml

ROOT = Path(__file__).resolve().parents[1]
TEXT_XML = (ROOT / "data" / "samples" / "TEXTDATA.TDS").read_text(encoding="utf-8")
IMAGE_XML = (ROOT / "data" / "samples" / "IMAGEDATA.IDS").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def isolate_dictionary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dictionary = json.loads((ROOT / "data" / "dynamic_dictionary.json").read_text(encoding="utf-8"))
    dictionary["rules"] = {
        rule_id: rule for rule_id, rule in dictionary.get("rules", {}).items()
        if not rule.get("source")
    }
    dictionary["history"] = []
    path = tmp_path / "dynamic_dictionary.json"
    path.write_text(json.dumps(dictionary), encoding="utf-8")
    monkeypatch.setattr(engine, "DICT_PATH", path)
    monkeypatch.setattr(llm_worker, "DICT_PATH", path)


def test_green_direct_pass() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)
    doc.text_entries = [
        item for item in doc.text_entries if item.legacy_format in {"Supply Voltage %4d V", "Engine Speed %4d RPM"}
    ]
    doc.image_entries = [item for item in doc.image_entries if item.file_name == "TORTUGA.BMP"]

    result = engine.run_migration(doc, scenario="demo")

    assert result.tier == "Green"
    assert "<ScreenRepository" in result.srd_xml
    assert "FileFormat" in result.srd_xml
    assert result.translation_trace[0]["route"] == "Case B · dictionary"


def test_amber_self_healing_loop() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)
    result = engine.run_migration(doc, scenario="demo")

    assert result.tier == "Green"
    assert any("Self-healing" in log for log in result.logs)
    assert "Hydraulic Temp %+04d C" in "\n".join(result.logs)
    learned_text = next(item for item in result.translation_trace if item["input"]["match"] == "Hydraulic Temp %+04d C")
    assert learned_text["route"] == "Case A · mock_llm"
    assert learned_text["rule_output"]
    gauge = next(item for item in result.translation_trace if item["input"]["match"] == "GAUGE_RPM.BMP")
    assert 'Width="200"' in gauge["generated_srd"]
    assert 'Height="200"' in gauge["generated_srd"]


def test_red_safety_veto() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)
    for item in doc.text_entries:
        if item.legacy_format.startswith("Hydraulic Temp"):
            item.can_signal = "CAN_HYD_FAULT"
            item.polarity = "INVERTED"
    result = engine.run_migration(doc, scenario="hazard")

    assert result.tier == "Red"
    assert result.safety_veto is True
    assert result.score < 70


def test_sample_bitmaps_match_ids_dimensions() -> None:
    for file_name, expected_size in (("TORTUGA.BMP", (32, 32)), ("GAUGE_RPM.BMP", (100, 100))):
        image_path = ROOT / "static" / "sample-images" / file_name
        image_bytes = image_path.read_bytes()
        assert image_bytes[:2] == b"BM"
        dimensions = (
            int.from_bytes(image_bytes[18:22], "little", signed=True),
            int.from_bytes(image_bytes[22:26], "little", signed=True),
        )
        assert dimensions == expected_size


def test_custom_aspect_ratio_updates_generated_srd() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)

    result = engine.run_migration(doc, aspect_ratio_overrides={"I_002": (16, 9)})

    gauge = next(item for item in result.translation_trace if item["input"]["match"] == "GAUGE_RPM.BMP")
    assert 'Width="200"' in gauge["generated_srd"]
    assert 'Height="113"' in gauge["generated_srd"]
    assert 'AspectRatio="16:9"' in gauge["generated_srd"]
    assert 'LockAspect="true"' in gauge["generated_srd"]
    assert result.aspect_ratio_overrides["I_002"] == {"width": 16, "height": 9}


def test_custom_aspect_ratio_cannot_exceed_target_canvas() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)

    with pytest.raises(ValueError, match="outside the 800x480 target canvas"):
        engine.run_migration(doc, aspect_ratio_overrides={"I_002": (1, 1000)})
