from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import re
from typing import Mapping
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


@dataclass(frozen=True)
class ConversionSettings:
    source_width: int = 320
    source_height: int = 240
    target_width: int = 800
    target_height: int = 480
    text_font: str = "Roboto"
    text_size_scale: float = 2.0
    text_width: int = 500
    text_height: int = 60
    image_width: int = 96
    image_height: int = 96
    background: str = "0,0,0,0"

    def __post_init__(self) -> None:
        for name in ("source_width", "source_height", "target_width", "target_height"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.replace('_', ' ').capitalize()} must be greater than zero.")
        if self.text_size_scale <= 0:
            raise ValueError("Text size scale must be greater than zero.")
        for name in ("text_width", "text_height", "image_width", "image_height"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.replace('_', ' ').capitalize()} must be greater than zero.")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_xml(filename: str, content: bytes, expected_root: str) -> ET.Element:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"{filename}: invalid XML ({exc}).") from exc
    if _local_name(root.tag) != expected_root.lower():
        raise ValueError(f"{filename}: expected a {expected_root} root element, got {root.tag!r}.")
    return root


def _required_attribute(element: ET.Element, name: str, filename: str) -> str:
    value = element.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{filename}: <{_local_name(element.tag)}> is missing its {name!r} attribute.")
    return value.strip()


def _scaled(value: str, scale: float, filename: str, field: str) -> str:
    try:
        return str(round(float(value) * scale))
    except ValueError as exc:
        raise ValueError(f"{filename}: {field} must be a number, got {value!r}.") from exc


def _children(root: ET.Element, kind: str) -> list[ET.Element]:
    return [element for element in root.iter() if element is not root and _local_name(element.tag) == kind.lower()]


def convert_pair(
    text_xml: bytes,
    image_xml: bytes,
    settings: ConversionSettings = ConversionSettings(),
    text_filename: str = "textdata.xml",
    image_filename: str = "imgdata.xml",
) -> bytes:
    """Combine one TextData/ImageData pair into a DM700-style SRD XML document."""
    text_root = _parse_xml(text_filename, text_xml, "TextData")
    image_root = _parse_xml(image_filename, image_xml, "ImageData")
    text_scale_x = settings.target_width / settings.source_width
    text_scale_y = settings.target_height / settings.source_height
    screen = ET.Element(
        "Screen",
        {
            "id": "1",
            "width": str(settings.target_width),
            "height": str(settings.target_height),
            "background": settings.background,
        },
    )

    for source in _children(text_root, "Text"):
        element_id = _required_attribute(source, "id", text_filename)
        x = _required_attribute(source, "x", text_filename)
        y = _required_attribute(source, "y", text_filename)
        content = "".join(source.itertext()).strip()
        if not content:
            raise ValueError(f"{text_filename}: text element {element_id!r} has no text content.")
        target = ET.SubElement(
            screen,
            "Text",
            {
                "id": element_id,
                "x": _scaled(x, text_scale_x, text_filename, "x"),
                "y": _scaled(y, text_scale_y, text_filename, "y"),
                "width": str(settings.text_width),
                "height": str(settings.text_height),
                "font": settings.text_font,
                "size": _scaled(
                    _required_attribute(source, "size", text_filename),
                    settings.text_size_scale,
                    text_filename,
                    "size",
                ),
                "color": _required_attribute(source, "color", text_filename),
            },
        )
        target.text = content

    for source in _children(image_root, "Image"):
        element_id = _required_attribute(source, "id", image_filename)
        x = _required_attribute(source, "x", image_filename)
        y = _required_attribute(source, "y", image_filename)
        image_file = _required_attribute(source, "file", image_filename)
        image_file = PurePosixPath(image_file.replace("\\", "/")).name
        if not image_file:
            raise ValueError(f"{image_filename}: image element {element_id!r} has an invalid file path.")
        ET.SubElement(
            screen,
            "Image",
            {
                "id": element_id,
                "x": _scaled(x, text_scale_x, image_filename, "x"),
                "y": _scaled(y, text_scale_y, image_filename, "y"),
                "width": str(settings.image_width),
                "height": str(settings.image_height),
                "file": image_file,
            },
        )

    if not list(screen):
        raise ValueError("The XML files do not contain any Text or Image elements to convert.")
    return ET.tostring(screen, encoding="utf-8", xml_declaration=True)


def _group_key(filename: str, kind: str) -> str | None:
    stem = PurePosixPath(filename.replace("\\", "/")).stem
    match = re.fullmatch(rf"{kind}(.*)", stem, flags=re.IGNORECASE)
    return match.group(1).lstrip("_- ") if match else None


def convert_uploads(
    files: Mapping[str, bytes],
    settings: ConversionSettings = ConversionSettings(),
) -> dict[str, bytes]:
    """Convert uploaded XML pairs, pairing files by their textdata/imgdata filename suffix."""
    text_files: dict[str, tuple[str, bytes]] = {}
    image_files: dict[str, tuple[str, bytes]] = {}
    unclassified: list[tuple[str, bytes]] = []

    for filename, content in files.items():
        if PurePosixPath(filename.replace("\\", "/")).suffix.lower() != ".xml":
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ValueError(f"{filename}: invalid XML ({exc}).") from exc
        root_name = _local_name(root.tag)
        if root_name == "textdata":
            kind, target = "textdata", text_files
        elif root_name == "imagedata":
            kind, target = "imgdata", image_files
        else:
            raise ValueError(f"{filename}: expected a TextData or ImageData root element, got {root.tag!r}.")
        key = _group_key(filename, kind)
        if key is None:
            unclassified.append((filename, content))
            continue
        if key in target:
            raise ValueError(f"More than one {kind} XML file was uploaded for set {key!r}.")
        target[key] = (filename, content)

    if unclassified:
        if len(unclassified) != 2 or text_files or image_files:
            names = ", ".join(filename for filename, _ in unclassified)
            raise ValueError(
                f"Could not pair these XML files by name: {names}. "
                "Name each pair textdata1.xml/imgdata1.xml, textdata2.xml/imgdata2.xml, and so on."
            )
        for filename, content in unclassified:
            root = ET.fromstring(content)
            if _local_name(root.tag) == "textdata":
                text_files[""] = (filename, content)
            else:
                image_files[""] = (filename, content)

    missing_images = sorted(set(text_files) - set(image_files))
    missing_text = sorted(set(image_files) - set(text_files))
    if missing_images or missing_text:
        details = []
        if missing_images:
            details.append(f"missing imgdata file(s) for set(s): {', '.join(missing_images)}")
        if missing_text:
            details.append(f"missing textdata file(s) for set(s): {', '.join(missing_text)}")
        raise ValueError("Cannot convert: " + "; ".join(details) + ".")
    if not text_files:
        raise ValueError("Upload at least one TextData XML and its matching ImageData XML.")

    results: dict[str, bytes] = {}
    for key in sorted(text_files):
        text_filename, text_content = text_files[key]
        image_filename, image_content = image_files[key]
        result_name = f"screen{key}.srd" if key else "screen1.srd"
        results[result_name] = convert_pair(
            text_content,
            image_content,
            settings,
            text_filename=text_filename,
            image_filename=image_filename,
        )
    return results


def create_download(
    srds: Mapping[str, bytes],
    assets: Mapping[str, bytes] | None = None,
) -> tuple[str, str, bytes]:
    """Return a single SRD download or a ZIP when there are multiple screens/assets."""
    safe_assets = {
        PurePosixPath(name.replace("\\", "/")).name: content
        for name, content in (assets or {}).items()
        if PurePosixPath(name.replace("\\", "/")).name
    }
    if len(srds) == 1 and not safe_assets:
        filename, content = next(iter(srds.items()))
        return filename, "application/xml", content

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in srds.items():
            archive.writestr(PurePosixPath(name.replace("\\", "/")).name, content)
        for name, content in safe_assets.items():
            archive.writestr(name, content)
    return "converted_screens.zip", "application/zip", buffer.getvalue()
