from __future__ import annotations

from typing import List
from xml.etree import ElementTree as ET

from app.models import LegacyImageAsset, LegacyTextEntry, UIRDocument


def _text(element: ET.Element, *paths: str) -> str:
    for path in paths:
        value = element.findtext(path)
        if value is not None and value.strip():
            return value.strip()
    return ""


def _optional_int(element: ET.Element, attribute: str, *paths: str) -> int | None:
    value = element.get(attribute) or _text(element, *paths)
    return int(value) if value else None


def parse_legacy_xml(text_xml: str, image_xml: str) -> UIRDocument:
    text_root = ET.fromstring(text_xml)
    image_root = ET.fromstring(image_xml)

    text_entries: List[LegacyTextEntry] = []
    for node in text_root.iter():
        if node.tag != "Resource":
            continue
        line_id = f"Line_{len(text_entries) + 1:03d}"
        entry_id = node.get("ID") or node.get("Id") or node.get("Name") or "TEXT_UNNAMED"
        description = _text(node, "Description", "Name", "Caption")
        legacy_format = _text(node, "Text", "Format", "LegacyFormat", "Value")
        can_signal = _text(node, "CAN", "CANSignal", "Signal", "SignalName") or None
        polarity = _text(node, "Polarity", "SignalPolarity", "CustomAlarmPolarity") or "NORMAL"
        text_entries.append(
            LegacyTextEntry(
                id=entry_id,
                line_id=line_id,
                description=description,
                legacy_format=legacy_format,
                source_file="TEXTDATA.TDS",
                x=_optional_int(node, "X", "X", "PosX"),
                y=_optional_int(node, "Y", "Y", "PosY"),
                width=_optional_int(node, "Width", "W", "Width"),
                height=_optional_int(node, "Height", "H", "Height"),
                can_signal=can_signal,
                polarity=polarity.upper(),
            )
        )

    image_entries: List[LegacyImageAsset] = []
    for node in image_root.iter():
        if node.tag != "ImageResource":
            continue
        line_id = f"Line_{len(text_entries) + len(image_entries) + 1:03d}"
        asset_id = node.get("ID") or node.get("Id") or node.get("Name") or "IMG_UNNAMED"
        file_name = _text(node, "SourceFileName", "FileName", "Name")
        x = _optional_int(node, "X", "X", "PosX")
        y = _optional_int(node, "Y", "Y", "PosY")
        width = _optional_int(node, "Width", "W", "Width")
        height = _optional_int(node, "Height", "H", "Height")
        color_depth = int(_text(node, "ColorDepth") or 1)
        output_format = _text(node, "OutputFormat", "Format") or "RLE"
        can_signal = _text(node, "CAN", "CANSignal", "Signal", "SignalName") or None
        polarity = _text(node, "Polarity", "SignalPolarity", "CustomAlarmPolarity") or "NORMAL"
        image_entries.append(
            LegacyImageAsset(
                id=asset_id,
                line_id=line_id,
                source_file="IMAGEDATA.IDS",
                file_name=file_name,
                x=x,
                y=y,
                width=width,
                height=height,
                color_depth=color_depth,
                output_format=output_format,
                can_signal=can_signal,
                polarity=polarity.upper(),
            )
        )

    return UIRDocument(text_entries=text_entries, image_entries=image_entries)
