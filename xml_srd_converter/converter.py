from __future__ import annotations

import copy
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import re
import struct
from typing import Callable, Mapping
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


def _direct_child(element: ET.Element, kind: str) -> ET.Element | None:
    return next((child for child in element if _local_name(child.tag) == kind.lower()), None)


def _required_child_text(element: ET.Element, kind: str, filename: str) -> str:
    child = _direct_child(element, kind)
    if child is None or child.text is None or not child.text.strip():
        raise ValueError(f"{filename}: <{_local_name(element.tag)}> is missing <{kind}>.")
    return child.text.strip()


def _parse_source_xml(filename: str, content: bytes, expected_root: str) -> ET.Element:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"{filename}: invalid XML ({exc}).") from exc
    if _local_name(root.tag) != expected_root.lower():
        raise ValueError(f"{filename}: expected a {expected_root} root element, got {root.tag!r}.")
    return root


_FORMAT_CONVERSION = re.compile(r"%([-+ #0]*)(\d*)(?:\.(\d+))?([hlL]*)([diuxX])")


def _convert_format_string(value: str, filename: str) -> tuple[str, list[dict[str, str]]]:
    output: list[str] = []
    formats: list[dict[str, str]] = []
    index = 0
    while index < len(value):
        if value[index] != "%":
            output.append(value[index])
            index += 1
            continue
        if value[index:index + 2] == "%%":
            output.append("%")
            index += 2
            continue
        match = _FORMAT_CONVERSION.match(value, index)
        if match is None:
            raise ValueError(
                f"{filename}: unsupported or incomplete format specifier near {value[index:index + 12]!r}. "
                "The converter supports %d, %i, %u, %x, and %X integer formats."
            )
        flags, width, _precision, _length, conversion = match.groups()
        formats.append({
            "Type": "Unsigned" if conversion == "u" else "Signed",
            "Digits": width or "1",
            "Value": "0",
            "Prefix": "2" if "+" in flags else "1" if " " in flags else "0",
            "Padding": "3" if "0" in flags else "1",
            "Base": {"d": "1", "i": "1", "u": "1", "x": "2", "X": "3"}[conversion],
            "Alt": "true" if "#" in flags else "false",
        })
        output.append(f"%{len(formats)}$")
        index = match.end()
    return "".join(output), formats


def _convert_tds_text(source: ET.Element, filename: str) -> ET.Element:
    result = ET.Element("Text")
    for tag in ("ID", "Description", "Used_by"):
        child = _direct_child(source, tag)
        if child is not None:
            result.append(copy.deepcopy(child))
    if _direct_child(result, "Used_by") is None:
        ET.SubElement(result, "Used_by")

    source_strings = [child for child in source if _local_name(child.tag) == "string"]
    if not source_strings:
        raise ValueError(f"{filename}: text resource has no <String> entries.")

    reference_formats: list[dict[str, str]] | None = None
    for source_string in source_strings:
        text = source_string.get("TextData")
        if text is None:
            raise ValueError(f"{filename}: <String> is missing its TextData attribute.")
        converted, formats = _convert_format_string(text, filename)
        if reference_formats is None:
            reference_formats = formats
        elif [
            (item["Type"], item["Base"]) for item in formats
        ] != [
            (item["Type"], item["Base"]) for item in reference_formats
        ]:
            raise ValueError(f"{filename}: language strings use different numeric format specifiers.")
        result_string = ET.SubElement(result, "String")
        if source_string.get("LanguageIndex") is not None:
            result_string.set("LanguageIndex", source_string.attrib["LanguageIndex"])
        result_string.set("TextData", converted)

    if reference_formats is None:
        raise ValueError(f"{filename}: text resource has no language strings.")
    if reference_formats:
        inputs = ET.SubElement(result, "Inputs", {"Count": str(len(reference_formats))})
        for attributes in reference_formats:
            ET.SubElement(inputs, "FC", attributes)
    return result


def _bmp_dimensions(filename: str, content: bytes) -> tuple[int, int]:
    if len(content) < 26 or content[:2] != b"BM":
        raise ValueError(f"{filename}: expected a valid BMP image to determine image dimensions.")
    dib_size = struct.unpack_from("<I", content, 14)[0]
    if dib_size == 12:
        width, height = struct.unpack_from("<HH", content, 18)
    elif dib_size >= 40:
        width, signed_height = struct.unpack_from("<ii", content, 18)
        height = abs(signed_height)
    else:
        raise ValueError(f"{filename}: unsupported BMP header; cannot determine image dimensions.")
    if width <= 0 or height <= 0:
        raise ValueError(f"{filename}: BMP dimensions must be greater than zero.")
    return width, height


def _convert_ids_image(
    source: ET.Element,
    filename: str,
    assets_by_name: Mapping[str, tuple[str, bytes]],
) -> ET.Element:
    source_filename = _required_child_text(source, "SourceFileName", filename)
    asset = assets_by_name.get(PurePosixPath(source_filename.replace("\\", "/")).name.casefold())
    if asset is None:
        raise ValueError(
            f"{filename}: upload the referenced image asset {source_filename!r}; "
            "the .IDS file does not contain its dimensions."
        )
    asset_filename, asset_content = asset
    width, height = _bmp_dimensions(asset_filename, asset_content)

    color_depth = _required_child_text(source, "ColorDepth", filename)
    try:
        depth = int(color_depth)
    except ValueError as exc:
        raise ValueError(f"{filename}: invalid image ColorDepth {color_depth!r}.") from exc
    if depth <= 0 or depth > 32:
        raise ValueError(f"{filename}: unsupported image ColorDepth {depth}.")

    result = ET.Element("Image")
    for tag in ("ID", "Description"):
        child = _direct_child(source, tag)
        if child is not None:
            result.append(copy.deepcopy(child))
    ET.SubElement(result, "SourceFileName").text = source_filename
    ET.SubElement(result, "Width").text = str(width)
    ET.SubElement(result, "Height").text = str(height)
    ET.SubElement(result, "FlipH").text = "false"
    ET.SubElement(result, "FlipV").text = "false"
    format_name = {
        1: "Monochrome (1 bit)",
        4: "16 Colors (4 bit)",
        8: "256 Colors (8 bit)",
        16: "65536 Colors (16 bit)",
        24: "16777216 Colors (24 bit)",
    }.get(depth, f"{2 ** depth} Colors ({depth} bit)")
    ET.SubElement(result, "ImageFormatName").text = format_name

    pixel_origo = _direct_child(source, "PixelOrigo")
    ET.SubElement(result, "PixelOrigo").text = (
        pixel_origo.text.strip() if pixel_origo is not None and pixel_origo.text else "0"
    )
    for tag, defaults in (
        ("InsertionPoint", {"X": "0", "Y": "0"}),
        ("Transparent", {"PaletteColor": "", "X": "0", "Y": "0", "LowLeft": "0"}),
    ):
        source_element = _direct_child(source, tag)
        attributes = dict(defaults)
        if source_element is not None:
            attributes.update(source_element.attrib)
        ET.SubElement(result, tag, attributes)
    return result


def _copy_library_contents(
    source: ET.Element,
    target: ET.Element,
    resource_tag: str,
    filename: str,
    transform: Callable[..., ET.Element],
    *,
    flatten_main: bool = False,
    assets_by_name: Mapping[str, tuple[str, bytes]] | None = None,
) -> None:
    for child in source:
        tag = _local_name(child.tag)
        if tag == "folder":
            folder_name = child.get("Name", "Main")
            if flatten_main and folder_name.casefold() == "main":
                _copy_library_contents(
                    child, target, resource_tag, filename, transform,
                    assets_by_name=assets_by_name,
                )
            else:
                folder = ET.SubElement(target, "Folder", {"Name": folder_name})
                _copy_library_contents(
                    child, folder, resource_tag, filename, transform,
                    assets_by_name=assets_by_name,
                )
        elif tag == resource_tag.lower():
            if resource_tag.lower() == "image":
                target.append(transform(child, filename, assets_by_name or {}))
            else:
                target.append(transform(child, filename))


def convert_resource_pair(
    text_data: bytes,
    image_data: bytes,
    assets: Mapping[str, bytes],
    text_filename: str = "TEXTDATA.TDS",
    image_filename: str = "IMAGEDATA.IDS",
) -> bytes:
    """Convert TDS/IDS resource catalogs to the ScreenRepository SRD format."""
    text_root = _parse_source_xml(text_filename, text_data, "TextDataSource")
    image_root = _parse_source_xml(image_filename, image_data, "ImageDataSource")

    assets_by_name: dict[str, tuple[str, bytes]] = {}
    for asset_filename, content in assets.items():
        key = PurePosixPath(asset_filename.replace("\\", "/")).name.casefold()
        if key in assets_by_name:
            raise ValueError(f"More than one uploaded asset has the filename {key!r}.")
        assets_by_name[key] = (asset_filename, content)

    repository = ET.Element("ScreenRepository")
    ET.SubElement(repository, "FileFormat").text = "006A"
    library = ET.SubElement(repository, "ScreenLibrary", {"Name": "Library"})
    main_folder = ET.SubElement(library, "Folder", {"Name": "Main"})
    _copy_library_contents(
        text_root, main_folder, "Text", text_filename, _convert_tds_text, flatten_main=True
    )
    _copy_library_contents(
        image_root, main_folder, "Image", image_filename, _convert_ids_image,
        flatten_main=True, assets_by_name=assets_by_name,
    )

    language_definition = ET.SubElement(
        library,
        "LanguageDefinition",
        {
            "DefLang": text_root.get("DefLang", "1"),
            "OrgLang": text_root.get("OrgLang", "1"),
        },
    )
    for language in text_root:
        if _local_name(language.tag) == "language":
            language_definition.append(copy.deepcopy(language))

    ET.SubElement(repository, "ScreenDefRepository")
    ET.SubElement(repository, "ApplogDefRepository")
    ET.indent(repository, space="  ")
    return ET.tostring(repository, encoding="utf-8", xml_declaration=True)


def _group_key(filename: str, kind: str) -> str | None:
    stem = PurePosixPath(filename.replace("\\", "/")).stem
    match = re.fullmatch(rf"{kind}(.*)", stem, flags=re.IGNORECASE)
    return match.group(1).lstrip("_- ") if match else None


def convert_uploads(
    files: Mapping[str, bytes],
    settings: ConversionSettings = ConversionSettings(),
) -> dict[str, bytes]:
    """Convert TDS/IDS catalogs or the earlier simple XML sample format."""
    resource_text: list[tuple[str, bytes]] = []
    resource_images: list[tuple[str, bytes]] = []
    assets: dict[str, bytes] = {}
    text_files: dict[str, tuple[str, bytes]] = {}
    image_files: dict[str, tuple[str, bytes]] = {}
    unclassified: list[tuple[str, bytes]] = []

    for filename, content in files.items():
        suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        if suffix in {".bmp", ".png", ".jpg", ".jpeg", ".gif"}:
            assets[filename] = content
            continue
        if suffix not in {".xml", ".tds", ".ids"}:
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ValueError(f"{filename}: invalid XML ({exc}).") from exc
        root_name = _local_name(root.tag)
        if root_name == "textdatasource":
            resource_text.append((filename, content))
            continue
        elif root_name == "imagedatasource":
            resource_images.append((filename, content))
            continue
        elif root_name == "textdata":
            kind, target = "textdata", text_files
        elif root_name == "imagedata":
            kind, target = "imgdata", image_files
        else:
            raise ValueError(
                f"{filename}: expected TextDataSource (.TDS), ImageDataSource (.IDS), "
                f"TextData, or ImageData XML; got {root.tag!r}."
            )
        key = _group_key(filename, kind)
        if key is None:
            unclassified.append((filename, content))
            continue
        if key in target:
            raise ValueError(f"More than one {kind} XML file was uploaded for set {key!r}.")
        target[key] = (filename, content)

    if resource_text or resource_images:
        if text_files or image_files or unclassified:
            raise ValueError("Do not mix TDS/IDS resource files with the earlier simple XML format.")
        if len(resource_text) != 1 or len(resource_images) != 1:
            raise ValueError("Upload exactly one TextDataSource (.TDS) and one ImageDataSource (.IDS) file.")
        text_filename, text_content = resource_text[0]
        image_filename, image_content = resource_images[0]
        return {
            "ScreenRepository.srd": convert_resource_pair(
                text_content,
                image_content,
                assets,
                text_filename=text_filename,
                image_filename=image_filename,
            )
        }

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
        raise ValueError("Upload a .TDS and .IDS pair, or a TextData and ImageData XML pair.")

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
