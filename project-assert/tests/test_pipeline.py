from __future__ import annotations

import json
import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import app.engine as engine
import app.llm_worker as llm_worker
from app.gate_scorer import validate_migration
from app.parser import parse_legacy_xml
import app.vision_worker as vision_worker

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


def test_green_direct_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)
    doc.text_entries = [
        item for item in doc.text_entries if item.legacy_format in {"Supply Voltage %4d V", "Engine Speed %4d RPM"}
    ]
    doc.image_entries = [item for item in doc.image_entries if item.file_name == "TORTUGA.BMP"]
    monkeypatch.setattr(
        engine,
        "infer_screen_layout",
        lambda _image, items, _assets: {
            item["line_id"]: {
                "x": 10,
                "y": 10 + index * 25,
                "width": 80,
                "height": 20,
                "ocr_text": item["match"].replace("%4d", "123").replace("%+04d", "123"),
            }
            for index, item in enumerate(items)
            if item["type"] == "text"
        },
    )

    result = engine.run_migration(doc, scenario="demo", screen_image_base64="dGVzdA==")

    assert result.validation is True
    assert "score" not in result.model_dump()
    assert "tier" not in result.model_dump()
    assert "<ScreenRepository" in result.srd_xml
    assert "FileFormat" in result.srd_xml
    assert result.translation_trace[0]["route"] == "Case B · deterministic dictionary"
    assert [flag.line_id for flag in result.line_flags] == ["Line_001", "Line_002", "Line_004"]
    assert all(flag.valid and flag.flag == "PASS" for flag in result.line_flags)
    assert all(set(flag.checks) == {"syntax", "safety", "layout", "traceability"} for flag in result.line_flags)
    upscaled = Image.open(BytesIO(base64.b64decode(result.upscaled_assets["TORTUGA_DM710.png"])))
    assert upscaled.size == (80, 64)
    assert 'File="TORTUGA_DM710.png"' in result.translation_trace[2]["generated_srd"]
    assert 'LegacyFile="TORTUGA.BMP"' in result.translation_trace[2]["generated_srd"]
    inputs = [entry["input"] for entry in result.translation_trace]
    tampered_xml = result.srd_xml.replace('Signal="SUPPLY_VOLTAGE"', 'Signal="WRONG_SIGNAL"', 1)
    assert validate_migration(inputs, tampered_xml, 0) is False


def test_unknown_rules_use_one_llm_fallback_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)
    monkeypatch.setattr(
        engine,
        "infer_screen_layout",
        lambda _image, items, _assets: {
            item["line_id"]: {
                "x": 10,
                "y": 10 + index * 25,
                "width": 80,
                "height": 20,
                "ocr_text": item["match"].replace("%4d", "123").replace("%+04d", "123"),
            }
            for index, item in enumerate(items)
            if item["type"] == "text"
        },
    )
    result = engine.run_migration(doc, scenario="demo", screen_image_base64="dGVzdA==")

    assert result.validation is True
    assert sum("learned via mock_llm" in log for log in result.logs) == 2
    assert "Hydraulic Temp %+04d C" in "\n".join(result.logs)
    assert not any("score" in log.lower() or "iteration" in log.lower() for log in result.logs)
    learned_text = next(item for item in result.translation_trace if item["input"]["match"] == "Hydraulic Temp %+04d C")
    assert learned_text["route"] == "Case A · mock_llm fallback"
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

    assert result.validation is False
    assert result.safety_veto is True
    assert any("Boolean Math Gate" in log for log in result.logs)


def test_boolean_validation_rejects_malformed_srd_xml() -> None:
    assert validate_migration([], "<ScreenRepository>", 0) is False


def test_line_ids_are_assigned_across_both_legacy_sources() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)

    assert [item.line_id for item in doc.text_entries + doc.image_entries] == [
        "Line_001", "Line_002", "Line_003", "Line_004", "Line_005"
    ]


def test_vision_worker_normalizes_detected_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": json.dumps({
                "elements": [
                    {
                        "line_id": "Line_001",
                        "x": 100,
                        "y": 200,
                        "width": 500,
                        "height": 250,
                        "ocr_text": "Oil pressure 40",
                    },
                    {
                        "line_id": "Line_002",
                        "asset_file": "TORTUGA.BMP",
                        "x": 600,
                        "y": 400,
                        "width": 100,
                        "height": 100,
                    },
                ]
            })}

    image = base64.b64encode((ROOT / "static" / "sample-images" / "TORTUGA.BMP").read_bytes()).decode("ascii")
    request_payload: dict[str, object] = {}

    def fake_post(_url: str, json: dict[str, object], timeout: float) -> FakeResponse:
        request_payload.update(json)
        return FakeResponse()

    monkeypatch.setattr(vision_worker.httpx, "post", fake_post)

    result = vision_worker.infer_screen_layout(
        image,
        [
            {"line_id": "Line_001", "type": "text", "match": "Oil pressure"},
            {"line_id": "Line_002", "type": "image", "match": "TORTUGA.BMP"},
        ],
        {"TORTUGA.BMP": image},
    )

    assert result["Line_001"] == {"x": 32, "y": 48, "width": 160, "height": 60, "ocr_text": "Oil pressure 40"}
    assert result["Line_002"] == {"x": 192, "y": 96, "width": 32, "height": 24}
    assert len(request_payload["images"]) == 2


def test_missing_text_geometry_is_routed_to_review() -> None:
    doc = parse_legacy_xml(TEXT_XML, IMAGE_XML)

    result = engine.run_migration(doc, scenario="demo")

    assert result.validation is False
    assert all(line.flag == "REVIEW" for line in result.line_flags if line.resource_type == "text")
    assert all(not line.checks["layout"] for line in result.line_flags if line.resource_type == "text")


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
