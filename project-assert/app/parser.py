from __future__ import annotations

from typing import Any, Dict, List
from xml.etree import ElementTree as ET

from app.models import LegacyImageAsset, LegacyTextEntry, UIRDocument


def _text(element: ET.Element, *paths: str) -> str:
    for path in paths:
        value = element.findtext(path)
        if value is not None and value.strip():
            return value.strip()
    return ""


def parse_legacy_xml(text_xml: str, image_xml: str) -> UIRDocument:
    text_root = ET.fromstring(text_xml)
    image_root = ET.fromstring(image_xml)

    text_entries: List[LegacyTextEntry] = []
    for node in text_root.iter():
        if node.tag != "Resource":
            continue
        entry_id = node.get("ID") or node.get("Id") or node.get("Name") or "TEXT_UNNAMED"
        description = _text(node, "Description", "Name", "Caption")
        legacy_format = _text(node, "Text", "Format", "LegacyFormat", "Value")
        can_signal = _text(node, "CAN", "CANSignal", "Signal", "SignalName") or None
        polarity = _text(node, "Polarity", "SignalPolarity", "CustomAlarmPolarity") or "NORMAL"
        text_entries.append(
            LegacyTextEntry(
                id=entry_id,
                description=description,
                legacy_format=legacy_format,
                source_file="TEXTDATA.TDS",
                can_signal=can_signal,
                polarity=polarity.upper(),
            )
        )

    image_entries: List[LegacyImageAsset] = []
    for node in image_root.iter():
        if node.tag != "ImageResource":
            continue
        asset_id = node.get("ID") or node.get("Id") or node.get("Name") or "IMG_UNNAMED"
        file_name = _text(node, "SourceFileName", "FileName", "Name")
        x = int(_text(node, "X", "PosX") or 0)
        y = int(_text(node, "Y", "PosY") or 0)
        width = int(_text(node, "W", "Width") or 0)
        height = int(_text(node, "H", "Height") or 0)
        color_depth = int(_text(node, "ColorDepth") or 1)
        output_format = _text(node, "OutputFormat", "Format") or "RLE"
        can_signal = _text(node, "CAN", "CANSignal", "Signal", "SignalName") or None
        polarity = _text(node, "Polarity", "SignalPolarity", "CustomAlarmPolarity") or "NORMAL"
        image_entries.append(
            LegacyImageAsset(
                id=asset_id,
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
