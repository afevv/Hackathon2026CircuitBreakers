from __future__ import annotations

import xml.etree.ElementTree as ET
import math
from typing import Any, Dict, Iterable, List


def _scale(value: int, factor: float) -> int:
    return int(round(value * factor))


def generate_srd_xml(
    document: Any,
    applied_rules: Iterable[Dict[str, Any]],
    aspect_ratio_overrides: Dict[str, tuple[int, int]] | None = None,
) -> str:
    aspect_ratio_overrides = aspect_ratio_overrides or {}
    root = ET.Element("ScreenRepository")
    ET.SubElement(root, "FileFormat").text = "006A"
    ET.SubElement(root, "LanguageDefinition").text = "en-US"
    library = ET.SubElement(root, "ScreenLibrary")

    for text in document.text_entries:
        fc = ET.SubElement(library, "FC")
        fc.set("ID", f"FC_{text.id}")
        fc.set("Source", text.source_file)
        fc.set("Legacy", text.legacy_format)
        fc.set("Signal", text.can_signal or "GENERIC_TEXT")
        fc.set("Polarity", text.polarity)
        fc.set("Provenance", f"{text.source_file}|{text.id}")
        ET.SubElement(fc, "Description").text = text.description or text.legacy_format
        ET.SubElement(fc, "Format").text = text.legacy_format

    for image in document.image_entries:
        is_gauge = "GAUGE" in image.file_name.upper()
        width_scale = 2.0 if is_gauge else 2.5
        height_scale = 2.0
        output_width = _scale(image.width, width_scale)
        output_height = _scale(image.height, height_scale)
        output_x = _scale(image.x, 2.5)
        output_y = _scale(image.y, 2.0)
        ratio = aspect_ratio_overrides.get(image.id)
        if ratio is not None:
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
        divisor = math.gcd(ratio_width, ratio_height)
        aspect_ratio = f"{ratio_width // divisor}:{ratio_height // divisor}"
        if output_width < 1 or output_height < 1 or output_x + output_width > 800 or output_y + output_height > 480:
            raise ValueError(f"Generated image {image.file_name} does not fit inside the 800x480 target canvas.")
        img = ET.SubElement(library, "Image")
        img.set("ID", f"IMG_{image.id}")
        img.set("Source", image.source_file)
        img.set("File", image.file_name)
        img.set("Signal", image.can_signal or "GENERIC_IMAGE")
        img.set("Polarity", image.polarity)
        img.set("Provenance", f"{image.source_file}|{image.id}")
        img.set("X", str(output_x))
        img.set("Y", str(output_y))
        img.set("Width", str(output_width))
        img.set("Height", str(output_height))
        img.set("AspectRatio", aspect_ratio)
        img.set("LockAspect", "true" if is_locked else "false")

    return ET.tostring(root, encoding="unicode")
