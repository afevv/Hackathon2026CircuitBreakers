from __future__ import annotations

import xml.etree.ElementTree as ET
import math
import re
from typing import Any, Dict, Iterable


def _scale(value: int, factor: float) -> int:
    return int(round(value * factor))


def _indexed_format(value: str) -> tuple[str, str | None, int | None]:
    token = re.search(r"%(?P<sign>\+)?0?(?P<width>\d*)d", value)
    if token is None:
        return value, None, None
    digits = int(token.group("width") or 0) + 1
    return value[:token.start()] + "%1$" + value[token.end():], "Signed", digits


def generate_srd_xml(
    document: Any,
    applied_rules: Iterable[Dict[str, Any]],
    aspect_ratio_overrides: Dict[str, tuple[int, int]] | None = None,
) -> str:
    aspect_ratio_overrides = aspect_ratio_overrides or {}
    rules_by_line = {
        rule["line_id"]: rule
        for rule in applied_rules
        if rule.get("line_id")
    }
    root = ET.Element("ScreenRepository")
    ET.SubElement(root, "FileFormat").text = "006A"
    ET.SubElement(root, "LanguageDefinition").text = "en-US"
    library = ET.SubElement(root, "ScreenLibrary")

    for text in document.text_entries:
        rule = rules_by_line.get(text.line_id, {})
        fc = ET.SubElement(library, "FC")
        fc.set("ID", f"FC_{text.id}")
        fc.set("LineID", text.line_id)
        if rule.get("rule_id"):
            fc.set("RuleID", rule["rule_id"])
        fc.set("Source", text.source_file)
        fc.set("Legacy", text.legacy_format)
        fc.set("Signal", text.can_signal or "GENERIC_TEXT")
        fc.set("Polarity", text.polarity)
        fc.set("Provenance", f"{text.source_file}|{text.id}")
        ET.SubElement(fc, "Description").text = text.description or text.legacy_format
        ET.SubElement(fc, "Format").text = text.legacy_format
        indexed_text, format_type, digits = _indexed_format(text.legacy_format)
        string_node = ET.SubElement(fc, "String")
        string_node.set("LanguageIndex", "1")
        string_node.set("TextData", rule.get("target_pattern") or indexed_text)
        if format_type is not None and digits is not None:
            inputs = ET.SubElement(fc, "Inputs")
            inputs.set("Count", "1")
            ET.SubElement(inputs, "FC", {
                "Type": format_type,
                "Digits": str(digits),
                "Value": "0",
                "Prefix": "0",
                "Padding": "1",
                "Base": "1",
                "Alt": "false",
            })
        if text.x is not None and text.y is not None and text.width is not None and text.height is not None:
            geometry = ET.SubElement(fc, "Geometry")
            geometry.set("X", str(_scale(text.x, 2.5)))
            geometry.set("Y", str(_scale(text.y, 2.0)))
            geometry.set("Width", str(_scale(text.width, 2.5)))
            geometry.set("Height", str(_scale(text.height, 2.0)))
            geometry.set("PixelOrigo", "TopLeft")

    for image in document.image_entries:
        rule = rules_by_line.get(image.line_id, {})
        is_gauge = "GAUGE" in image.file_name.upper()
        width_scale = 2.0 if is_gauge else 2.5
        height_scale = 2.0
        has_geometry = image.x is not None and image.y is not None and image.width is not None and image.height is not None
        output_width = _scale(image.width, width_scale) if image.width is not None else 0
        output_height = _scale(image.height, height_scale) if image.height is not None else 0
        output_x = _scale(image.x, 2.5) if image.x is not None else 0
        output_y = _scale(image.y, 2.0) if image.y is not None else 0
        ratio = aspect_ratio_overrides.get(image.id)
        if ratio is not None and output_width > 0:
            ratio_width, ratio_height = ratio
            if ratio_width <= 0 or ratio_height <= 0:
                raise ValueError(f"Aspect ratio for {image.file_name} must be positive.")
            output_height = math.floor(output_width * ratio_height / ratio_width + 0.5)
            if output_height < 1 or output_y + output_height > 480:
                raise ValueError(f"Aspect ratio for {image.file_name} places it outside the 800x480 target canvas.")
            is_locked = True
        else:
            is_locked = is_gauge
            ratio_width, ratio_height = output_width, output_height
        if ratio_width > 0 and ratio_height > 0:
            divisor = math.gcd(ratio_width, ratio_height)
            aspect_ratio = f"{ratio_width // divisor}:{ratio_height // divisor}"
        else:
            aspect_ratio = ""
        if ratio is not None and output_width > 0 and output_height > 0:
            if output_x + output_width > 800 or output_y + output_height > 480:
                raise ValueError(f"Aspect ratio for {image.file_name} places it outside the 800x480 target canvas.")
        img = ET.SubElement(library, "Image")
        img.set("ID", f"IMG_{image.id}")
        img.set("LineID", image.line_id)
        if rule.get("rule_id"):
            img.set("RuleID", rule["rule_id"])
        img.set("Source", image.source_file)
        img.set("File", image.file_name)
        img.set("ImageFormatName", "Monochrome (1 bit)" if image.color_depth == 1 else "TrueColor")
        img.set("Signal", image.can_signal or "GENERIC_IMAGE")
        img.set("Polarity", image.polarity)
        img.set("Provenance", f"{image.source_file}|{image.id}")
        if has_geometry:
            img.set("X", str(output_x))
            img.set("Y", str(output_y))
            img.set("Width", str(output_width))
            img.set("Height", str(output_height))
            img.set("AspectRatio", aspect_ratio)
            img.set("LockAspect", "true" if is_locked else "false")
            geometry = ET.SubElement(img, "Geometry")
            geometry.set("X", str(output_x))
            geometry.set("Y", str(output_y))
            geometry.set("Width", str(output_width))
            geometry.set("Height", str(output_height))
            geometry.set("PixelOrigo", "TopLeft")

    return ET.tostring(root, encoding="unicode")
