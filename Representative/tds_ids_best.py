#!/usr/bin/env python3

"""
Direct-translation TDS + IDS to SDR preprocessor.

The converter performs deterministic translations only. It does not ask an
LLM to repeat work that Python can perform reliably.

Outputs
-------
1. An intermediate SDR XML file.
2. A JSON migration manifest containing unresolved tasks for a future LLM.

ID rules
--------
1. TDS Text IDs are copied exactly.
2. TDS Text IDs are never renumbered.
3. IDS Image IDs are shifted by the number of TDS Text elements.

Example:
    TDS IDs: 4, 1, 2, 3
    TDS Text count: 4

    IDS Image ID 1 -> SDR Image ID 5
    IDS Image ID 2 -> SDR Image ID 6

Deterministic work performed
----------------------------
- Preserve TDS IDs.
- Shift IDS IDs.
- Preserve folder order and structure.
- Convert printf placeholders to %1$, %2$, etc.
- Count text inputs.
- Validate CodePage against DefaultCodePage.
- Read BMP width and height directly from the BMP header.
- Copy language definitions.
- Copy PixelOrigo and InsertionPoint.
- Set FlipH and FlipV to false.
- Detect duplicate IDs and output ID collisions.

Work left for the future LLM or another migration stage
-------------------------------------------------------
- Resolve FC attributes.
- Resolve semantic image names.
- Coordinate image filename changes.
- Resolve ImageFormatName.
- Resolve transparency differences.
- Resolve source-only image properties.
- Build ScreenDefRepository when layout data becomes available.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


PRINTF_PATTERN = re.compile(
    r"%(?P<flags>[-+ #0]*)"
    r"(?P<width>\d+)?"
    r"(?:\.(?P<precision>\d+))?"
    r"(?P<length>hh|h|ll|l|j|z|t|L)?"
    r"(?P<conversion>[diuoxXfFeEgGaAcsp%])"
)


# ---------------------------------------------------------------------------
# XML utilities
# ---------------------------------------------------------------------------

def safe_comment_text(text: str) -> str:
    """
    Make a string safe for use inside an XML comment.

    XML comments cannot contain two consecutive hyphens.
    """

    return text.replace("--", "- -")


def add_review_comment(
    parent: ET.Element,
    message: str,
) -> None:
    """
    Add a descriptive review marker.

    The # prefix means a later migration stage must process the item.
    """

    comment = safe_comment_text(
        f" # {message} "
    )

    parent.append(
        ET.Comment(comment)
    )


def indent_xml(
    element: ET.Element,
    level: int = 0,
) -> None:
    """
    Apply readable indentation to an ElementTree structure.
    """

    current_indent = "\n" + "  " * level
    child_indent = "\n" + "  " * (level + 1)

    if len(element):
        if not element.text or not element.text.strip():
            element.text = child_indent

        for child in element:
            indent_xml(child, level + 1)

            if not child.tail or not child.tail.strip():
                child.tail = child_indent

        element[-1].tail = current_indent

    elif level and (
        not element.tail or not element.tail.strip()
    ):
        element.tail = current_indent


def element_to_single_line_xml(
    element: ET.Element,
) -> str:
    """
    Serialize an XML element onto one line.
    """

    xml_text = ET.tostring(
        element,
        encoding="unicode",
        short_empty_elements=True,
    )

    return " ".join(
        xml_text.split()
    )


def add_empty_unresolved_element(
    parent: ET.Element,
    tag_name: str,
    reason: str,
) -> ET.Element:
    """
    Add an empty SDR element and a review marker.
    """

    element = ET.SubElement(
        parent,
        tag_name,
    )

    add_review_comment(
        parent,
        reason,
    )

    return element


# ---------------------------------------------------------------------------
# File and input validation
# ---------------------------------------------------------------------------

def parse_xml_file(
    file_path: Path,
) -> ET.Element:
    """
    Parse an XML file and return its root element.
    """

    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file does not exist: {file_path}"
        )

    if not file_path.is_file():
        raise ValueError(
            f"Input path is not a file: {file_path}"
        )

    try:
        return ET.parse(
            file_path
        ).getroot()

    except ET.ParseError as error:
        raise ValueError(
            f"Invalid XML in {file_path}: {error}"
        ) from error


def read_resource_id(
    resource: ET.Element,
    resource_name: str,
) -> int:
    """
    Read and validate an ID from a Text or Image resource.
    """

    id_element = resource.find("ID")

    if id_element is None:
        raise ValueError(
            f"{resource_name} is missing an ID element."
        )

    if id_element.text is None:
        raise ValueError(
            f"{resource_name} contains an empty ID."
        )

    id_text = id_element.text.strip()

    try:
        resource_id = int(id_text)

    except ValueError as error:
        raise ValueError(
            f"{resource_name} has a non-numeric ID: "
            f"{id_text!r}"
        ) from error

    if resource_id < 1:
        raise ValueError(
            f"{resource_name} has an invalid ID: {resource_id}. "
            "IDs must be at least 1."
        )

    return resource_id


def validate_unique_ids(
    resources: list[ET.Element],
    resource_name: str,
) -> None:
    """
    Ensure that IDs are unique within one source file.
    """

    seen_ids: set[int] = set()

    for resource in resources:
        resource_id = read_resource_id(
            resource,
            resource_name,
        )

        if resource_id in seen_ids:
            raise ValueError(
                f"Duplicate {resource_name} ID: {resource_id}"
            )

        seen_ids.add(resource_id)


def validate_output_ids(
    tds_texts: list[ET.Element],
    ids_images: list[ET.Element],
    image_id_offset: int,
) -> None:
    """
    Ensure that shifted image IDs do not overlap with TDS IDs.
    """

    tds_ids = {
        read_resource_id(text, "TDS Text")
        for text in tds_texts
    }

    shifted_image_ids = {
        read_resource_id(image, "IDS Image") + image_id_offset
        for image in ids_images
    }

    collisions = tds_ids.intersection(
        shifted_image_ids
    )

    if collisions:
        values = ", ".join(
            str(resource_id)
            for resource_id in sorted(collisions)
        )

        raise ValueError(
            "The image ID offset produces collisions with TDS IDs. "
            f"Conflicting IDs: {values}"
        )


# ---------------------------------------------------------------------------
# Migration manifest
# ---------------------------------------------------------------------------

def create_manifest(
    tds_path: Path,
    ids_path: Path,
    output_path: Path,
    image_directory: Path | None,
) -> dict[str, Any]:
    """
    Create the JSON migration manifest.
    """

    return {
        "manifest_version": 1,
        "purpose": (
            "Tasks not completed by the deterministic "
            "TDS/IDS to SDR converter"
        ),
        "source_files": {
            "tds": str(tds_path),
            "ids": str(ids_path),
            "image_directory": (
                str(image_directory)
                if image_directory is not None
                else None
            ),
        },
        "target_file": str(output_path),
        "tasks": [],
    }


def add_manifest_task(
    manifest: dict[str, Any],
    *,
    task_type: str,
    resource_type: str,
    resource_id: int | None,
    description: str,
    target: dict[str, Any],
    source: dict[str, Any] | None = None,
    required_output: dict[str, Any] | None = None,
    reason: str,
) -> None:
    """
    Add one unresolved migration task to the manifest.
    """

    task_number = len(
        manifest["tasks"]
    ) + 1

    task: dict[str, Any] = {
        "task_id": task_number,
        "task_type": task_type,
        "status": "unresolved",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "description": description,
        "target": target,
        "reason": reason,
    }

    if source is not None:
        task["source"] = source

    if required_output is not None:
        task["required_output"] = required_output

    manifest["tasks"].append(task)


# ---------------------------------------------------------------------------
# Language and CodePage validation
# ---------------------------------------------------------------------------

def get_language_code_pages(
    tds_root: ET.Element,
) -> dict[str, str]:
    """
    Return DefaultCodePage values indexed by language index.

    Example:
        {
            "1": "1252",
            "2": "1252"
        }
    """

    result: dict[str, str] = {}

    for language in tds_root.findall("Language"):
        index = language.findtext("Index")
        code_page = language.findtext(
            "DefaultCodePage"
        )

        if index is None or code_page is None:
            continue

        result[index.strip()] = code_page.strip()

    return result


def code_page_is_resolved(
    source_string: ET.Element,
    language_code_pages: dict[str, str],
) -> tuple[bool, str]:
    """
    Check whether a String-level CodePage agrees with the relevant
    Language DefaultCodePage.

    Returns:
        resolved
        explanation
    """

    string_code_page = source_string.get(
        "CodePage"
    )

    if string_code_page is None:
        return True, "String does not specify CodePage"

    language_index = source_string.get(
        "LanguageIndex",
        "",
    )

    default_code_page = language_code_pages.get(
        language_index
    )

    if default_code_page is None:
        return (
            False,
            (
                f'No DefaultCodePage exists for '
                f'LanguageIndex="{language_index}"'
            ),
        )

    if string_code_page == default_code_page:
        return (
            True,
            (
                f'String CodePage="{string_code_page}" matches '
                f'Language DefaultCodePage="{default_code_page}"'
            ),
        )

    return (
        False,
        (
            f'String CodePage="{string_code_page}" differs from '
            f'Language DefaultCodePage="{default_code_page}"'
        ),
    )


# ---------------------------------------------------------------------------
# Printf conversion
# ---------------------------------------------------------------------------

def rewrite_format_fields(
    text_data: str,
) -> tuple[
    str,
    list[str],
    list[str],
]:
    """
    Replace recognized printf fields with SDR positional fields.

    Examples:
        %4d   -> %1$
        %04d  -> %1$
        %+04d -> %1$
        %4x   -> %1$

    Returns:
        rewritten text
        translated printf fields
        untranslated printf fields
    """

    translated_fields: list[str] = []
    untranslated_fields: list[str] = []

    def replace_field(
        match: re.Match[str],
    ) -> str:
        conversion = match.group(
            "conversion"
        )

        source_field = match.group(0)

        # %% is a literal percent sign, not an input.
        if conversion == "%":
            return "%"

        # Pointer conversion does not have a confirmed mapping.
        if conversion == "p":
            untranslated_fields.append(
                source_field
            )

            return source_field

        translated_fields.append(
            source_field
        )

        field_number = len(
            translated_fields
        )

        return f"%{field_number}$"

    rewritten_text = PRINTF_PATTERN.sub(
        replace_field,
        text_data,
    )

    return (
        rewritten_text,
        translated_fields,
        untranslated_fields,
    )


# ---------------------------------------------------------------------------
# BMP metadata
# ---------------------------------------------------------------------------

def find_bitmap_path(
    source_filename: str,
    image_directory: Path | None,
    ids_path: Path,
) -> Path | None:
    """
    Find a referenced bitmap.

    Search order:
    1. --image-dir, when supplied.
    2. The directory containing the IDS file.

    A case-insensitive fallback is used because Windows filenames are
    case-insensitive while other filesystems may not be.
    """

    search_directories: list[Path] = []

    if image_directory is not None:
        search_directories.append(
            image_directory
        )

    ids_directory = ids_path.parent

    if ids_directory not in search_directories:
        search_directories.append(
            ids_directory
        )

    for directory in search_directories:
        direct_path = directory / source_filename

        if direct_path.is_file():
            return direct_path

        if not directory.is_dir():
            continue

        lowered_name = source_filename.lower()

        for candidate in directory.iterdir():
            if (
                candidate.is_file()
                and candidate.name.lower() == lowered_name
            ):
                return candidate

    return None


def read_bmp_dimensions(
    bitmap_path: Path,
) -> tuple[int, int]:
    """
    Read BMP width and height directly from the BMP header.

    This uses only Python's standard library.

    Supported:
    - Windows BITMAPINFOHEADER and later formats
    - OS/2 BITMAPCOREHEADER
    """

    with bitmap_path.open("rb") as bitmap_file:
        signature = bitmap_file.read(2)

        if signature != b"BM":
            raise ValueError(
                f"Not a BMP file: {bitmap_path}"
            )

        bitmap_file.seek(14)

        dib_header_size_bytes = bitmap_file.read(4)

        if len(dib_header_size_bytes) != 4:
            raise ValueError(
                f"Invalid BMP header: {bitmap_path}"
            )

        dib_header_size = struct.unpack(
            "<I",
            dib_header_size_bytes,
        )[0]

        if dib_header_size == 12:
            # OS/2 BITMAPCOREHEADER
            size_data = bitmap_file.read(4)

            if len(size_data) != 4:
                raise ValueError(
                    f"Invalid OS/2 BMP header: {bitmap_path}"
                )

            width, height = struct.unpack(
                "<HH",
                size_data,
            )

        elif dib_header_size >= 40:
            # Windows BITMAPINFOHEADER or later
            size_data = bitmap_file.read(8)

            if len(size_data) != 8:
                raise ValueError(
                    f"Invalid Windows BMP header: {bitmap_path}"
                )

            width, signed_height = struct.unpack(
                "<ii",
                size_data,
            )

            # A negative height denotes a top-down bitmap.
            height = abs(signed_height)

        else:
            raise ValueError(
                f"Unsupported BMP DIB header size "
                f"{dib_header_size}: {bitmap_path}"
            )

    if width <= 0 or height <= 0:
        raise ValueError(
            f"Invalid BMP dimensions "
            f"{width}x{height}: {bitmap_path}"
        )

    return width, height


# ---------------------------------------------------------------------------
# Text conversion
# ---------------------------------------------------------------------------

def convert_text(
    source_text: ET.Element,
    language_code_pages: dict[str, str],
    manifest: dict[str, Any],
    warnings: list[str],
) -> ET.Element:
    """
    Convert one TDS Text resource.
    """

    output_text = ET.Element("Text")

    source_id_element = source_text.find(
        "ID"
    )

    if source_id_element is None:
        raise ValueError(
            "A TDS Text element is missing its ID."
        )

    # TDS ID is copied exactly.
    output_text.append(
        copy.deepcopy(source_id_element)
    )

    text_id = read_resource_id(
        source_text,
        "TDS Text",
    )

    description_element = source_text.find(
        "Description"
    )

    description = (
        description_element.text.strip()
        if (
            description_element is not None
            and description_element.text is not None
        )
        else ""
    )

    if description_element is not None:
        output_text.append(
            copy.deepcopy(description_element)
        )
    else:
        add_empty_unresolved_element(
            output_text,
            "Description",
            "Description is missing from the source TDS Text",
        )

        add_manifest_task(
            manifest,
            task_type="missing_text_description",
            resource_type="Text",
            resource_id=text_id,
            description="",
            target={
                "element": "Description",
            },
            reason=(
                "The source Text resource does not contain "
                "a Description"
            ),
        )

    used_by = source_text.find(
        "Used_by"
    )

    if used_by is not None:
        output_text.append(
            copy.deepcopy(used_by)
        )
    else:
        add_empty_unresolved_element(
            output_text,
            "Used_by",
            "Used_by is missing from the source TDS Text",
        )

    canonical_fields: list[str] | None = None
    language_field_sets: list[dict[str, Any]] = []

    for source_string in source_text.findall(
        "String"
    ):
        language_index = source_string.get(
            "LanguageIndex",
            "",
        )

        source_text_data = source_string.get(
            "TextData",
            "",
        )

        (
            rewritten_text,
            translated_fields,
            untranslated_fields,
        ) = rewrite_format_fields(
            source_text_data
        )

        ET.SubElement(
            output_text,
            "String",
            {
                "LanguageIndex": language_index,
                "TextData": rewritten_text,
            },
        )

        code_page_resolved, explanation = (
            code_page_is_resolved(
                source_string,
                language_code_pages,
            )
        )

        if not code_page_resolved:
            add_review_comment(
                output_text,
                explanation,
            )

            add_manifest_task(
                manifest,
                task_type="resolve_code_page",
                resource_type="Text",
                resource_id=text_id,
                description=description,
                target={
                    "element": "String",
                    "language_index": language_index,
                },
                source={
                    "string_code_page": source_string.get(
                        "CodePage"
                    ),
                    "language_default_code_page": (
                        language_code_pages.get(
                            language_index
                        )
                    ),
                },
                reason=explanation,
            )

        language_field_sets.append(
            {
                "language_index": language_index,
                "translated_fields": translated_fields,
                "untranslated_fields": untranslated_fields,
            }
        )

        if canonical_fields is None:
            canonical_fields = translated_fields

        elif canonical_fields != translated_fields:
            warnings.append(
                f"TDS Text ID {text_id}: languages use "
                "different printf fields."
            )

        if untranslated_fields:
            add_review_comment(
                output_text,
                (
                    "Unsupported printf field(s) preserved: "
                    + ", ".join(untranslated_fields)
                ),
            )

            add_manifest_task(
                manifest,
                task_type="resolve_unsupported_printf",
                resource_type="Text",
                resource_id=text_id,
                description=description,
                target={
                    "element": "String",
                    "language_index": language_index,
                },
                source={
                    "text_data": source_text_data,
                    "unsupported_fields": untranslated_fields,
                },
                reason=(
                    "One or more printf fields do not have "
                    "a deterministic translation"
                ),
            )

    canonical_fields = canonical_fields or []

    if canonical_fields:
        inputs = ET.SubElement(
            output_text,
            "Inputs",
            {
                "Count": str(
                    len(canonical_fields)
                ),
            },
        )

        for input_index, source_format in enumerate(
            canonical_fields,
            start=1,
        ):
            ET.SubElement(
                inputs,
                "FC",
                {
                    "Type": "",
                    "Digits": "",
                    "Value": "",
                    "Prefix": "",
                    "Padding": "",
                    "Base": "",
                    "Alt": "",
                },
            )

            add_review_comment(
                inputs,
                (
                    f'FC {input_index} unresolved; '
                    f'source format="{source_format}"'
                ),
            )

            add_manifest_task(
                manifest,
                task_type="resolve_fc",
                resource_type="Text",
                resource_id=text_id,
                description=description,
                target={
                    "element": "FC",
                    "input_index": input_index,
                },
                source={
                    "printf_format": source_format,
                    "language_field_sets": (
                        language_field_sets
                    ),
                },
                required_output={
                    "Type": (
                        "Target FC type"
                    ),
                    "Digits": (
                        "Target FC digits value"
                    ),
                    "Value": (
                        "Target FC default value"
                    ),
                    "Prefix": (
                        "Target FC prefix value"
                    ),
                    "Padding": (
                        "Target FC padding value"
                    ),
                    "Base": (
                        "Target FC numeric base value"
                    ),
                    "Alt": (
                        "Target FC alternate-form boolean"
                    ),
                    "confidence": (
                        "Number from 0.0 to 1.0"
                    ),
                    "evidence": (
                        "List of source facts or known examples"
                    ),
                },
                reason=(
                    "The target FC attribute values cannot yet "
                    "be proven from a deterministic mapping"
                ),
            )

    handled_tags = {
        "ID",
        "Description",
        "Used_by",
        "String",
    }

    for child in source_text:
        if child.tag in handled_tags:
            continue

        source_xml = element_to_single_line_xml(
            child
        )

        add_review_comment(
            output_text,
            (
                "Untranslated source Text element: "
                + source_xml
            ),
        )

        add_manifest_task(
            manifest,
            task_type="resolve_source_text_element",
            resource_type="Text",
            resource_id=text_id,
            description=description,
            target={
                "element": None,
            },
            source={
                "xml": source_xml,
            },
            reason=(
                "The source element has no confirmed direct "
                "SDR translation"
            ),
        )

    return output_text


# ---------------------------------------------------------------------------
# Image conversion
# ---------------------------------------------------------------------------

def read_image_description(
    source_image: ET.Element,
) -> str:
    """
    Return the source image description.
    """

    description = source_image.findtext(
        "Description"
    )

    return (
        description.strip()
        if description is not None
        else ""
    )


def copy_image_description(
    source_image: ET.Element,
    output_image: ET.Element,
    output_image_id: int,
    manifest: dict[str, Any],
) -> str:
    """
    Preserve the source image description and create a semantic
    renaming task.
    """

    description_element = source_image.find(
        "Description"
    )

    description = read_image_description(
        source_image
    )

    if description_element is None:
        add_empty_unresolved_element(
            output_image,
            "Description",
            "Image Description is missing from the IDS",
        )

        add_manifest_task(
            manifest,
            task_type="missing_image_description",
            resource_type="Image",
            resource_id=output_image_id,
            description="",
            target={
                "element": "Description",
            },
            reason=(
                "The source IDS Image does not contain "
                "a Description"
            ),
        )

        return ""

    output_image.append(
        copy.deepcopy(description_element)
    )

    add_review_comment(
        output_image,
        (
            "Image Description preserved; semantic renaming "
            "requires a later stage"
        ),
    )

    add_manifest_task(
        manifest,
        task_type="review_image_description",
        resource_type="Image",
        resource_id=output_image_id,
        description=description,
        target={
            "element": "Description",
            "current_value": description,
        },
        source={
            "description": description,
        },
        required_output={
            "action": (
                'Either "keep" or "replace"'
            ),
            "value": (
                "Replacement description when action is replace"
            ),
            "confidence": (
                "Number from 0.0 to 1.0"
            ),
            "evidence": (
                "List of supporting facts"
            ),
        },
        reason=(
            "The objective example semantically renamed "
            "image descriptions"
        ),
    )

    return description


def copy_image_filename(
    source_image: ET.Element,
    output_image: ET.Element,
    output_image_id: int,
    description: str,
    manifest: dict[str, Any],
) -> str:
    """
    Preserve SourceFileName and create a coordinated rename task.
    """

    filename_element = source_image.find(
        "SourceFileName"
    )

    if (
        filename_element is None
        or filename_element.text is None
    ):
        add_empty_unresolved_element(
            output_image,
            "SourceFileName",
            "SourceFileName is missing from the IDS",
        )

        add_manifest_task(
            manifest,
            task_type="missing_image_filename",
            resource_type="Image",
            resource_id=output_image_id,
            description=description,
            target={
                "element": "SourceFileName",
            },
            reason=(
                "The source IDS Image does not contain "
                "a SourceFileName"
            ),
        )

        return ""

    source_filename = filename_element.text.strip()

    output_image.append(
        copy.deepcopy(filename_element)
    )

    add_review_comment(
        output_image,
        (
            "SourceFileName preserved; any target rename must "
            "also rename or copy the physical bitmap"
        ),
    )

    add_manifest_task(
        manifest,
        task_type="review_image_filename",
        resource_type="Image",
        resource_id=output_image_id,
        description=description,
        target={
            "element": "SourceFileName",
            "current_value": source_filename,
        },
        source={
            "filename": source_filename,
        },
        required_output={
            "action": (
                'Either "keep" or "replace"'
            ),
            "value": (
                "Replacement filename when action is replace"
            ),
            "physical_file_action": (
                'Either "none", "rename", or "copy"'
            ),
            "confidence": (
                "Number from 0.0 to 1.0"
            ),
        },
        reason=(
            "Changing the XML filename without changing the "
            "physical bitmap would break the reference"
        ),
    )

    return source_filename


def add_image_dimensions(
    source_filename: str,
    source_image: ET.Element,
    output_image: ET.Element,
    output_image_id: int,
    description: str,
    image_directory: Path | None,
    ids_path: Path,
    manifest: dict[str, Any],
    warnings: list[str],
) -> None:
    """
    Read image dimensions from the referenced BMP when possible.
    """

    if not source_filename:
        add_empty_unresolved_element(
            output_image,
            "Width",
            "Width unresolved because SourceFileName is missing",
        )

        add_empty_unresolved_element(
            output_image,
            "Height",
            "Height unresolved because SourceFileName is missing",
        )

        return

    bitmap_path = find_bitmap_path(
        source_filename,
        image_directory,
        ids_path,
    )

    if bitmap_path is None:
        add_empty_unresolved_element(
            output_image,
            "Width",
            (
                f'Width unresolved because bitmap '
                f'"{source_filename}" was not found'
            ),
        )

        add_empty_unresolved_element(
            output_image,
            "Height",
            (
                f'Height unresolved because bitmap '
                f'"{source_filename}" was not found'
            ),
        )

        add_manifest_task(
            manifest,
            task_type="read_bitmap_dimensions",
            resource_type="Image",
            resource_id=output_image_id,
            description=description,
            target={
                "elements": [
                    "Width",
                    "Height",
                ],
            },
            source={
                "filename": source_filename,
            },
            reason=(
                "The referenced bitmap was not available to "
                "the deterministic converter"
            ),
        )

        return

    try:
        width, height = read_bmp_dimensions(
            bitmap_path
        )

    except (OSError, ValueError) as error:
        warnings.append(
            f"Could not read dimensions from {bitmap_path}: "
            f"{error}"
        )

        add_empty_unresolved_element(
            output_image,
            "Width",
            (
                "Width unresolved because the BMP header "
                "could not be read"
            ),
        )

        add_empty_unresolved_element(
            output_image,
            "Height",
            (
                "Height unresolved because the BMP header "
                "could not be read"
            ),
        )

        add_manifest_task(
            manifest,
            task_type="read_bitmap_dimensions",
            resource_type="Image",
            resource_id=output_image_id,
            description=description,
            target={
                "elements": [
                    "Width",
                    "Height",
                ],
            },
            source={
                "filename": source_filename,
                "resolved_path": str(bitmap_path),
                "error": str(error),
            },
            reason=(
                "The bitmap exists but its dimensions could "
                "not be read"
            ),
        )

        return

    ET.SubElement(
        output_image,
        "Width",
    ).text = str(width)

    ET.SubElement(
        output_image,
        "Height",
    ).text = str(height)


def convert_transparency(
    source_image: ET.Element,
    output_image: ET.Element,
    output_image_id: int,
    description: str,
    manifest: dict[str, Any],
) -> None:
    """
    Preserve transparency attributes but create a review task.
    """

    transparent = source_image.find(
        "Transparent"
    )

    if transparent is None:
        add_empty_unresolved_element(
            output_image,
            "Transparent",
            "Transparent configuration is missing from IDS",
        )

        add_manifest_task(
            manifest,
            task_type="resolve_transparency",
            resource_type="Image",
            resource_id=output_image_id,
            description=description,
            target={
                "element": "Transparent",
            },
            reason=(
                "The source Image does not contain a "
                "Transparent element"
            ),
        )

        return

    output_image.append(
        copy.deepcopy(transparent)
    )

    add_review_comment(
        output_image,
        (
            "Transparent attributes preserved; target "
            "transparency behavior is not confirmed"
        ),
    )

    add_manifest_task(
        manifest,
        task_type="resolve_transparency",
        resource_type="Image",
        resource_id=output_image_id,
        description=description,
        target={
            "element": "Transparent",
            "current_attributes": dict(
                transparent.attrib
            ),
        },
        source={
            "attributes": dict(
                transparent.attrib
            ),
        },
        required_output={
            "PaletteColor": (
                "Target PaletteColor attribute"
            ),
            "X": (
                "Target X coordinate"
            ),
            "Y": (
                "Target Y coordinate"
            ),
            "LowLeft": (
                "Target LowLeft attribute"
            ),
            "confidence": (
                "Number from 0.0 to 1.0"
            ),
            "evidence": (
                "List of supporting facts"
            ),
        },
        reason=(
            "The objective example changes some transparency "
            "attributes, so direct copying is not confirmed"
        ),
    )


def convert_image(
    source_image: ET.Element,
    image_id_offset: int,
    image_directory: Path | None,
    ids_path: Path,
    manifest: dict[str, Any],
    warnings: list[str],
) -> ET.Element:
    """
    Convert one IDS Image resource.
    """

    output_image = ET.Element(
        "Image"
    )

    source_image_id = read_resource_id(
        source_image,
        "IDS Image",
    )

    output_image_id = (
        source_image_id + image_id_offset
    )

    ET.SubElement(
        output_image,
        "ID",
    ).text = str(output_image_id)

    description = copy_image_description(
        source_image,
        output_image,
        output_image_id,
        manifest,
    )

    source_filename = copy_image_filename(
        source_image,
        output_image,
        output_image_id,
        description,
        manifest,
    )

    add_image_dimensions(
        source_filename=source_filename,
        source_image=source_image,
        output_image=output_image,
        output_image_id=output_image_id,
        description=description,
        image_directory=image_directory,
        ids_path=ids_path,
        manifest=manifest,
        warnings=warnings,
    )

    # Explicit migration defaults.
    ET.SubElement(
        output_image,
        "FlipH",
    ).text = "false"

    ET.SubElement(
        output_image,
        "FlipV",
    ).text = "false"

    add_empty_unresolved_element(
        output_image,
        "ImageFormatName",
        (
            "ImageFormatName cannot be derived directly from "
            "IDS ColorDepth and OutputFormat"
        ),
    )

    color_depth = source_image.findtext(
        "ColorDepth"
    )

    output_format = source_image.findtext(
        "OutputFormat"
    )

    palette = source_image.find(
        "Palette"
    )

    palette_xml = (
        element_to_single_line_xml(palette)
        if palette is not None
        else None
    )

    add_manifest_task(
        manifest,
        task_type="resolve_image_format",
        resource_type="Image",
        resource_id=output_image_id,
        description=description,
        target={
            "element": "ImageFormatName",
        },
        source={
            "filename": source_filename,
            "ColorDepth": color_depth,
            "OutputFormat": output_format,
            "Palette": palette_xml,
        },
        required_output={
            "ImageFormatName": (
                "Exact target SDR string"
            ),
            "confidence": (
                "Number from 0.0 to 1.0"
            ),
            "evidence": (
                "List of supporting facts"
            ),
        },
        reason=(
            "The objective output does not use a simple "
            "one-to-one ColorDepth mapping"
        ),
    )

    pixel_origin = source_image.find(
        "PixelOrigo"
    )

    if pixel_origin is not None:
        output_image.append(
            copy.deepcopy(pixel_origin)
        )
    else:
        add_empty_unresolved_element(
            output_image,
            "PixelOrigo",
            "PixelOrigo is missing from IDS",
        )

    insertion_point = source_image.find(
        "InsertionPoint"
    )

    if insertion_point is not None:
        output_image.append(
            copy.deepcopy(insertion_point)
        )
    else:
        add_empty_unresolved_element(
            output_image,
            "InsertionPoint",
            "InsertionPoint is missing from IDS",
        )

    convert_transparency(
        source_image,
        output_image,
        output_image_id,
        description,
        manifest,
    )

    handled_tags = {
        "ID",
        "Description",
        "SourceFileName",
        "ColorDepth",
        "OutputFormat",
        "Palette",
        "PixelOrigo",
        "InsertionPoint",
        "Transparent",
    }

    for child in source_image:
        if child.tag in handled_tags:
            continue

        source_xml = element_to_single_line_xml(
            child
        )

        add_review_comment(
            output_image,
            (
                "Untranslated source Image element: "
                + source_xml
            ),
        )

        add_manifest_task(
            manifest,
            task_type="resolve_source_image_element",
            resource_type="Image",
            resource_id=output_image_id,
            description=description,
            target={
                "element": None,
            },
            source={
                "xml": source_xml,
            },
            reason=(
                "The source element has no confirmed direct "
                "SDR translation"
            ),
        )

    return output_image


# ---------------------------------------------------------------------------
# Folder conversion
# ---------------------------------------------------------------------------

def find_existing_folder(
    target_parent: ET.Element,
    folder_name: str,
) -> ET.Element | None:
    """
    Find a direct child Folder with a matching name.
    """

    for folder in target_parent.findall(
        "Folder"
    ):
        if folder.get("Name", "") == folder_name:
            return folder

    return None


def convert_tds_structure(
    source_parent: ET.Element,
    target_parent: ET.Element,
    language_code_pages: dict[str, str],
    manifest: dict[str, Any],
    warnings: list[str],
) -> None:
    """
    Convert TDS folders and Text resources in source order.
    """

    for child in source_parent:
        if child.tag == "Folder":
            output_folder = ET.SubElement(
                target_parent,
                "Folder",
                {
                    "Name": child.get(
                        "Name",
                        "",
                    ),
                },
            )

            convert_tds_structure(
                source_parent=child,
                target_parent=output_folder,
                language_code_pages=language_code_pages,
                manifest=manifest,
                warnings=warnings,
            )

        elif child.tag == "Text":
            target_parent.append(
                convert_text(
                    source_text=child,
                    language_code_pages=language_code_pages,
                    manifest=manifest,
                    warnings=warnings,
                )
            )


def convert_ids_structure(
    source_parent: ET.Element,
    target_parent: ET.Element,
    image_id_offset: int,
    image_directory: Path | None,
    ids_path: Path,
    manifest: dict[str, Any],
    warnings: list[str],
) -> None:
    """
    Convert IDS folders and Image resources in source order.
    """

    for child in source_parent:
        if child.tag == "Folder":
            folder_name = child.get(
                "Name",
                "",
            )

            output_folder = find_existing_folder(
                target_parent,
                folder_name,
            )

            if output_folder is None:
                output_folder = ET.SubElement(
                    target_parent,
                    "Folder",
                    {
                        "Name": folder_name,
                    },
                )

            convert_ids_structure(
                source_parent=child,
                target_parent=output_folder,
                image_id_offset=image_id_offset,
                image_directory=image_directory,
                ids_path=ids_path,
                manifest=manifest,
                warnings=warnings,
            )

        elif child.tag == "Image":
            target_parent.append(
                convert_image(
                    source_image=child,
                    image_id_offset=image_id_offset,
                    image_directory=image_directory,
                    ids_path=ids_path,
                    manifest=manifest,
                    warnings=warnings,
                )
            )


# ---------------------------------------------------------------------------
# Language conversion
# ---------------------------------------------------------------------------

def add_language_definition(
    tds_root: ET.Element,
    screen_library: ET.Element,
) -> None:
    """
    Convert TDS language information into LanguageDefinition.
    """

    language_definition = ET.SubElement(
        screen_library,
        "LanguageDefinition",
        {
            "DefLang": tds_root.get(
                "DefLang",
                "",
            ),
            "OrgLang": tds_root.get(
                "OrgLang",
                "",
            ),
        },
    )

    for language in tds_root.findall(
        "Language"
    ):
        language_definition.append(
            copy.deepcopy(language)
        )


# ---------------------------------------------------------------------------
# SDR construction
# ---------------------------------------------------------------------------

def build_sdr(
    tds_path: Path,
    ids_path: Path,
    output_path: Path,
    library_name: str,
    image_directory: Path | None,
) -> tuple[
    ET.ElementTree,
    dict[str, Any],
    list[str],
    int,
]:
    """
    Build the intermediate SDR and migration manifest.
    """

    tds_root = parse_xml_file(
        tds_path
    )

    ids_root = parse_xml_file(
        ids_path
    )

    if tds_root.tag != "TextDataSource":
        raise ValueError(
            f"Expected TextDataSource in {tds_path}, "
            f"but found {tds_root.tag}."
        )

    if ids_root.tag != "ImageDataSource":
        raise ValueError(
            f"Expected ImageDataSource in {ids_path}, "
            f"but found {ids_root.tag}."
        )

    tds_texts = list(
        tds_root.iter("Text")
    )

    ids_images = list(
        ids_root.iter("Image")
    )

    validate_unique_ids(
        tds_texts,
        "TDS Text",
    )

    validate_unique_ids(
        ids_images,
        "IDS Image",
    )

    # This value affects IDS IDs only.
    # TDS IDs are copied exactly.
    image_id_offset = len(
        tds_texts
    )

    validate_output_ids(
        tds_texts,
        ids_images,
        image_id_offset,
    )

    manifest = create_manifest(
        tds_path,
        ids_path,
        output_path,
        image_directory,
    )

    warnings: list[str] = []

    screen_repository = ET.Element(
        "ScreenRepository"
    )

    ET.SubElement(
        screen_repository,
        "FileFormat",
    ).text = "006A"

    add_review_comment(
        screen_repository,
        (
            'FileFormat value "006A" is example-derived '
            "and requires confirmation"
        ),
    )

    add_manifest_task(
        manifest,
        task_type="confirm_file_format",
        resource_type="Repository",
        resource_id=None,
        description="ScreenRepository",
        target={
            "element": "FileFormat",
            "current_value": "006A",
        },
        required_output={
            "action": (
                'Either "keep" or "replace"'
            ),
            "value": (
                "Replacement FileFormat when action is replace"
            ),
            "confidence": (
                "Number from 0.0 to 1.0"
            ),
            "evidence": (
                "List of supporting examples or specifications"
            ),
        },
        reason=(
            "006A appears in the supplied objective example "
            "but is not confirmed as universal"
        ),
    )

    screen_library = ET.SubElement(
        screen_repository,
        "ScreenLibrary",
        {
            "Name": library_name,
        },
    )

    if library_name == "Library":
        add_review_comment(
            screen_library,
            (
                'ScreenLibrary Name="Library" is a converter '
                "default rather than source-derived"
            ),
        )

        add_manifest_task(
            manifest,
            task_type="confirm_library_name",
            resource_type="ScreenLibrary",
            resource_id=None,
            description=library_name,
            target={
                "attribute": "Name",
                "current_value": library_name,
            },
            required_output={
                "action": (
                    'Either "keep" or "replace"'
                ),
                "value": (
                    "Replacement library name"
                ),
                "confidence": (
                    "Number from 0.0 to 1.0"
                ),
            },
            reason=(
                "The library name is not available from TDS or IDS"
            ),
        )

    language_code_pages = (
        get_language_code_pages(
            tds_root
        )
    )

    convert_tds_structure(
        source_parent=tds_root,
        target_parent=screen_library,
        language_code_pages=language_code_pages,
        manifest=manifest,
        warnings=warnings,
    )

    convert_ids_structure(
        source_parent=ids_root,
        target_parent=screen_library,
        image_id_offset=image_id_offset,
        image_directory=image_directory,
        ids_path=ids_path,
        manifest=manifest,
        warnings=warnings,
    )

    add_language_definition(
        tds_root,
        screen_library,
    )

    screen_def_repository = ET.SubElement(
        screen_repository,
        "ScreenDefRepository",
    )

    add_review_comment(
        screen_def_repository,
        (
            "ScreenDefRepository requires old screen-layout "
            "data not present in TDS or IDS"
        ),
    )

    add_manifest_task(
        manifest,
        task_type="supply_screen_layout_source",
        resource_type="ScreenDefRepository",
        resource_id=None,
        description="ScreenDefRepository",
        target={
            "element": "ScreenDefRepository",
        },
        reason=(
            "TDS and IDS contain reusable resources but do not "
            "contain widget positions, sizes, fonts, GUIDs, "
            "draw order, or screen interface connections"
        ),
    )

    ET.SubElement(
        screen_repository,
        "ApplogDefRepository",
    )

    add_review_comment(
        screen_repository,
        (
            "ApplogDefRepository is included from the objective "
            "example; no source mapping is available"
        ),
    )

    add_manifest_task(
        manifest,
        task_type="confirm_applog_repository",
        resource_type="ApplogDefRepository",
        resource_id=None,
        description="ApplogDefRepository",
        target={
            "element": "ApplogDefRepository",
        },
        reason=(
            "The element appears in the objective SDR but has "
            "no corresponding TDS or IDS source data"
        ),
    )

    indent_xml(
        screen_repository
    )

    manifest["summary"] = {
        "tds_text_count": len(tds_texts),
        "ids_image_count": len(ids_images),
        "image_id_offset": image_id_offset,
        "unresolved_task_count": len(
            manifest["tasks"]
        ),
    }

    return (
        ET.ElementTree(screen_repository),
        manifest,
        warnings,
        image_id_offset,
    )


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def default_manifest_path(
    output_path: Path,
) -> Path:
    """
    Calculate the default migration manifest path.

    output.sdr -> output.migration.json
    """

    return output_path.with_suffix(
        ".migration.json"
    )


def create_argument_parser() -> argparse.ArgumentParser:
    """
    Create command-line flags.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Perform deterministic TDS and IDS translations, "
            "write an intermediate SDR, and create a JSON "
            "manifest for unresolved migration tasks."
        )
    )

    parser.add_argument(
        "-t",
        "--tds",
        required=True,
        type=Path,
        metavar="TDS_FILE",
        help="Input TDS XML file",
    )

    parser.add_argument(
        "-i",
        "--ids",
        required=True,
        type=Path,
        metavar="IDS_FILE",
        help="Input IDS XML file",
    )

    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        metavar="SDR_FILE",
        help="Output intermediate SDR XML file",
    )

    parser.add_argument(
        "-m",
        "--manifest",
        type=Path,
        metavar="JSON_FILE",
        help=(
            "Output migration manifest. "
            "Default: OUTPUT_NAME.migration.json"
        ),
    )

    parser.add_argument(
        "-d",
        "--image-dir",
        type=Path,
        metavar="DIRECTORY",
        help=(
            "Directory containing BMP files. "
            "When omitted, the IDS file directory is searched."
        ),
    )

    parser.add_argument(
        "-l",
        "--library-name",
        default="Library",
        metavar="NAME",
        help=(
            'ScreenLibrary Name value. Default: "Library".'
        ),
    )

    return parser



# Optional translation and validation from testcodeThree.py
import json
import re
import struct
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
import urllib.request

try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TK = True
except ImportError:
    HAS_TK = False

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5:3b"

# Target DM710 display resolution
CANVAS_W = 800
CANVAS_H = 480

# Valid SRD tag whitelists for Metric 1 (Tags)
VALID_LIBRARY_TAGS = {
    "Text", "ID", "Description", "Used_by", "String", "Inputs", "FC",
    "Image", "SourceFileName", "Width", "Height", "FlipH", "FlipV",
    "ImageFormatName", "PixelOrigo", "InsertionPoint", "Transparent",
}
VALID_SCREENDEF_TAGS = {
    "Txt", "I", "La", "Pad", "LMO", "X", "Y", "W", "H", "En", "F",
    "GUID", "FF", "N", "S", "J", "T", "IsSink", "Output", "LocalX",
    "LocalY", "GType", "GX", "GY", "ZoomFactor", "DeltaX", "DeltaY",
    "Velocity", "RotAng", "LocalDeltaX", "LocalDeltaY", "C", "SP",
    "IP", "R", "O", "Pr", "Vis", "ID", "D", "DV",
}


def resolve_input_file(default_path: Path, title: str, filetypes: list[tuple[str, str]]) -> Path:
    """Finds the input XML in the script directory or opens a file picker."""
    if default_path.exists():
        return default_path
    if HAS_TK:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(title=title, filetypes=filetypes)
        root.destroy()
        if selected:
            return Path(selected)
    raise FileNotFoundError(f"Could not find required file: {default_path.name}")


def ai_read_bmp_dimensions(bmp_filename: str, search_dirs: list[Path]) -> tuple[int, int] | None:
    """
    Reads exact (width, height) from a BMP file header if the bitmap exists locally
    in any of the provided search directories.
    """
    if not bmp_filename:
        return None

    for folder in search_dirs:
        if not folder.exists():
            continue
        direct_path = folder / bmp_filename
        target_path = direct_path if direct_path.exists() else None

        if target_path is None:
            matches = [p for p in folder.iterdir() if p.is_file() and p.name.lower() == bmp_filename.lower()]
            if matches:
                target_path = matches[0]

        if target_path is not None:
            with target_path.open("rb") as f:
                header = f.read(26)
                if len(header) >= 26 and header[:2] == b"BM":
                    width, height = struct.unpack("<ii", header[18:26])
                    return abs(width), abs(height)
    return None


def call_ollama_json(system_prompt: str, user_prompt: str) -> dict:
    """Calls locally hosted Qwen 2.5 3B via Ollama with deterministic JSON output."""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
        },
    }
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(json.load(response)["message"]["content"])


def parse_dp600_files(tds_path: Path, ids_path: Path) -> tuple[list[dict], str]:
    """
    Reads TEXTDATA.TDS and IMAGEDATA.IDS and merges assets into a unified
    sequential ID space for DM710 SCREENREPO.SRD.
    """
    tds_root = ET.parse(tds_path).getroot()
    ids_root = ET.parse(ids_path).getroot()

    assets = []
    unified_id = 1

    def traverse_folders(node: ET.Element, folder_path: list[str], kind: str):
        nonlocal unified_id
        for child in node:
            if child.tag == "Folder":
                folder_name = child.attrib.get("Name", "Main")
                traverse_folders(child, folder_path + [folder_name], kind)
            elif child.tag == kind:
                raw_xml = ET.tostring(child, encoding="unicode").strip()
                desc = (child.findtext("Description") or f"{kind}_{unified_id}").strip()
                assets.append({
                    "link_id": f"SEC-{unified_id:03d}",
                    "unified_id": int(child.findtext("ID")) + (len(list(tds_root.iter("Text"))) if kind == "Image" else 0),
                    "original_id": (child.findtext("ID") or "").strip(),
                    "kind": kind,  # "Text" or "Image"
                    "description": desc,
                    "folder_path": folder_path if folder_path else ["Main"],
                    "raw_xml": raw_xml,
                    "element": child,
                })
                unified_id += 1

    traverse_folders(tds_root, [], "Text")
    traverse_folders(ids_root, [], "Image")

    def_lang = tds_root.attrib.get("DefLang", "1")
    org_lang = tds_root.attrib.get("OrgLang", "1")
    lang_entries = [
        f"      {ET.tostring(lang, encoding='unicode').strip()}"
        for lang in tds_root.findall("Language")
    ]
    lang_def_xml = (
        f'    <LanguageDefinition DefLang="{def_lang}" OrgLang="{org_lang}">\n'
        + "\n".join(lang_entries)
        + "\n    </LanguageDefinition>"
    )
    return assets, lang_def_xml


def try_preset_rule(asset: dict, slot_index: int) -> dict | None:
    """
    Preset rules handle trivial static text with zero printf format specifiers.
    Any dynamic printf string or image asset returns None so Qwen 2.5 3B handles it.
    """
    if asset["kind"] != "Text":
        return None

    el = asset["element"]
    strings = el.findall("String")
    if not strings:
        return None

    # Only match static strings containing no '%' format codes
    if any("%" in (s.attrib.get("TextData", "")) for s in strings):
        return None

    uid = asset["unified_id"]
    desc = asset["description"]
    x, y, w, h = 40, 25 + (slot_index * 45), 320, 21
    sx, sy = round(w / CANVAS_W, 6), round(h / CANVAS_H, 6)
    xp, yp = round((x / CANVAS_W) * 100, 4), round((y / CANVAS_H) * 100, 4)
    wp, hp = round(sx * 100, 4), round(sy * 100, 4)
    guid = "C" + uuid.uuid4().hex[:8].upper() + "_0000_4000_8000_" + uuid.uuid4().hex[:12].upper()

    str_tags = "\n".join(
        f'        <String LanguageIndex="{s.attrib.get("LanguageIndex", "1")}" TextData="{s.attrib.get("TextData", "")}"/>'
        for s in strings
    )
    library_xml = (
        f"<Text>\n"
        f"        <ID>{uid}</ID>\n"
        f"        <Description>{desc}</Description>\n"
        f"        <Used_by/>\n"
        f"{str_tags}\n"
        f"      </Text>"
    )
    screendef_xml = (
        f'<Txt N="{desc}_1" SX="{sx}" SY="{sy}" W="{w}" H="{h}" WP="{wp}" HP="{hp}" Type="TDS">\n'
        f'          <En V="True" E=""/>\n'
        f'          <GUID>{guid}</GUID>\n'
        f'          <FF><N>Courier New</N><S>14</S></FF>\n'
        f'          <C V="00000000" E=""/>\n'
        f'          <SP X="{x}" EX="" Y="{y}" EY="" XP="{xp}" YP="{yp}"/>\n'
        f'          <ID V="{uid}" E=""/>\n'
        f'        </Txt>'
    )
    return {
        "library_xml": library_xml,
        "screendef_xml": screendef_xml,
        "dimensions_estimated": False,
        "source": "PRESET_RULE",
    }


def translate_asset_with_llm(asset: dict, slot_index: int, search_dirs: list[Path]) -> dict:
    """
    Uses Qwen 2.5 3B to extract ONLY semantic translation primitives in a compact JSON schema,
    then deterministically builds well-formed DM710 SRD XML and computes exact 800x480 layout math.
    For images, reads exact .bmp dimensions from disk when available; otherwise uses LLM-estimated
    dimensions and flags dimensions_estimated=True for YELLOW engineer verification.
    """
    uid = asset["unified_id"]
    desc = asset["description"]
    src_el = asset["element"]
    x_pos = 40 if asset["kind"] == "Text" else 450
    y_pos = 25 + (slot_index * 50)
    guid = "C" + uuid.uuid4().hex[:8].upper() + "_1111_4000_8000_" + uuid.uuid4().hex[:12].upper()

    if asset["kind"] == "Text":
        system_prompt = """You are a Danfoss DP600-to-DM710 format translator. Convert C printf strings into VBSE positional strings and <FC> integer attributes.

VBSE <FC> RULES:
- prefix: 0 = None, 1 = Space/-, 2 = +/-
- padding: 0 = None, 1 = Left-padded spaces (e.g. %4d), 2 = Right-padded (%-4d), 3 = Leading zeros (%04d)
- base: 0 = Octal (%o), 1 = Decimal (%d), 2 = Hex lowercase (%x), 3 = Hex uppercase (%X)
- digits: integer width from printf specifier (default 4 if omitted)
- Unsupported C types like float (%f) or string (%s): set "supported": false.

EXAMPLES:
Input: "Supply Voltage %4d V" -> {"supported": true, "strings": {"1": "Supply Voltage %1$ V"}, "fc": [{"digits": 4, "prefix": 0, "padding": 1, "base": 1}]}
Input: "ShowPlusSign %+04d V" -> {"supported": true, "strings": {"1": "ShowPlusSign %1$ V"}, "fc": [{"digits": 4, "prefix": 2, "padding": 3, "base": 1}]}
Input: "Hexadecimal %4x" -> {"supported": true, "strings": {"1": "Hexadecimal %1$"}, "fc": [{"digits": 4, "prefix": 0, "padding": 1, "base": 2}]}

Respond ONLY in valid JSON:
{"supported": true, "strings": {"1": "...", "2": "..."}, "fc": [{"digits": int, "prefix": int, "padding": int, "base": int}]}"""

        user_prompt = f"Translate all LanguageIndex strings and printf format codes in this XML:\n{asset['raw_xml']}"

        try:
            data = call_ollama_json(system_prompt, user_prompt)
            if not data.get("supported", True):
                return {
                    "library_xml": "",
                    "screendef_xml": "",
                    "dimensions_estimated": False,
                    "error": "Unsupported printf specifier (e.g., float or string).",
                }

            strings_dict = data.get("strings", {})
            fc_list = data.get("fc", [])

            if not strings_dict:
                return {
                    "library_xml": "",
                    "screendef_xml": "",
                    "dimensions_estimated": False,
                    "error": "LLM returned empty strings dictionary.",
                }

            string_tags = "\n".join(
                f'        <String LanguageIndex="{lang}" TextData="{txt}"/>'
                for lang, txt in strings_dict.items()
            )

            fc_tags = "\n".join(
                f'          <FC Type="Signed" Digits="{int(fc["digits"])}" Value="0" '
                f'Prefix="{int(fc["prefix"])}" Padding="{int(fc["padding"])}" '
                f'Base="{int(fc["base"])}" Alt="false"/>'
                for fc in fc_list
            )
            inputs_block = (
                f'        <Inputs Count="{len(fc_list)}">\n{fc_tags}\n        </Inputs>'
                if fc_list else ""
            )

            library_xml = (
                f"<Text>\n"
                f"        <ID>{uid}</ID>\n"
                f"        <Description>{desc}</Description>\n"
                f"        <Used_by/>\n"
                f"{string_tags}\n"
                f"{inputs_block}\n"
                f"      </Text>"
            )

            # Calculate bounding box width from longest expanded multi-language string
            max_chars = 10
            for txt in strings_dict.values():
                expanded = txt
                for idx, fc in enumerate(fc_list, start=1):
                    expanded = expanded.replace(f"%{idx}$", "0" * int(fc.get("digits", 4)))
                max_chars = max(max_chars, len(expanded))

            w = min(CANVAS_W - x_pos, max(120, (max_chars + 2) * 11))
            h = 21
            sx = round(w / CANVAS_W, 6)
            sy = round(h / CANVAS_H, 6)
            xp = round((x_pos / CANVAS_W) * 100, 4)
            yp = round((y_pos / CANVAS_H) * 100, 4)
            wp = round(sx * 100, 4)
            hp = round(sy * 100, 4)

            dv_tags = "\n".join(
                f'            <DV V="0" E="1" FC="{idx}"/>' for idx in range(len(fc_list))
            )
            d_block = f"          <D>\n{dv_tags}\n          </D>" if fc_list else "          <D/>"

            screendef_xml = (
                f'<Txt N="{desc}_1" SX="{sx}" SY="{sy}" W="{w}" H="{h}" WP="{wp}" HP="{hp}" Type="TDS">\n'
                f'          <En V="True" E=""/>\n'
                f'          <GUID>{guid}</GUID>\n'
                f'          <FF><N>Courier New</N><S>14</S></FF>\n'
                f'          <C V="00000000" E=""/>\n'
                f'          <SP X="{x_pos}" EX="" Y="{y_pos}" EY="" XP="{xp}" YP="{yp}"/>\n'
                f'          <ID V="{uid}" E=""/>\n'
                f'{d_block}\n'
                f'        </Txt>'
            )
            return {
                "library_xml": library_xml,
                "screendef_xml": screendef_xml,
                "dimensions_estimated": False,
                "source": "LLM_QWEN_2.5_3B",
            }

        except Exception as exc:
            return {
                "library_xml": "",
                "screendef_xml": "",
                "dimensions_estimated": False,
                "source": "LLM_ERROR",
                "error": str(exc),
            }

    else:
        # Image asset translation with .bmp disk header lookup
        src_filename = (src_el.findtext("SourceFileName") or "").strip()
        bmp_dims = ai_read_bmp_dimensions(src_filename, search_dirs)
        dimensions_estimated = bmp_dims is None

        system_prompt = """You are a Danfoss DP600-to-DM710 image metadata mapper.
Map ColorDepth to ImageFormatName:
- ColorDepth 1 -> "Monochrome (1 bit)"
- ColorDepth 8 -> "256 Colors (8 bit)"
- ColorDepth 24 -> "16777216 Colors (24 bit)"

If known_bmp_dimensions is null, estimate icon pixel dimensions from Transparent Y + 1 (or default 48x32).

Respond ONLY in valid JSON:
{"image_format_name": "Monochrome (1 bit)"|"256 Colors (8 bit)"|"16777216 Colors (24 bit)", "width": int, "height": int}"""

        user_prompt = f"Map this DP600 <Image> XML (known_bmp_dimensions={bmp_dims}):\n{asset['raw_xml']}"

        try:
            data = call_ollama_json(system_prompt, user_prompt)
            fmt_name = data.get("image_format_name", "Monochrome (1 bit)")
            if bmp_dims is not None:
                w, h = bmp_dims
            else:
                w = max(16, int(data.get("width", 48)))
                h = max(16, int(data.get("height", 32)))

            ip_el = src_el.find("InsertionPoint")
            ip_x = ip_el.attrib.get("X", "0") if ip_el is not None else "0"
            ip_y = ip_el.attrib.get("Y", "0") if ip_el is not None else "0"

            tr_el = src_el.find("Transparent")
            tr_x = tr_el.attrib.get("X", "0") if tr_el is not None else "0"
            tr_y = tr_el.attrib.get("Y", "0") if tr_el is not None else "0"
            tr_ll = tr_el.attrib.get("LowLeft", "1") if tr_el is not None else "1"
            origo = (src_el.findtext("PixelOrigo") or "0").strip()

            library_xml = (
                f"<Image>\n"
                f"        <ID>{uid}</ID>\n"
                f"        <Description>{desc}</Description>\n"
                f"        <SourceFileName>{src_filename}</SourceFileName>\n"
                f"        <Width>{w}</Width>\n"
                f"        <Height>{h}</Height>\n"
                f"        <FlipH>false</FlipH>\n"
                f"        <FlipV>false</FlipV>\n"
                f"        <ImageFormatName>{fmt_name}</ImageFormatName>\n"
                f"        <PixelOrigo>{origo}</PixelOrigo>\n"
                f'        <InsertionPoint X="{ip_x}" Y="{ip_y}"/>\n'
                f'        <Transparent PaletteColor="" X="{tr_x}" Y="{tr_y}" LowLeft="{tr_ll}"/>\n'
                f"      </Image>"
            )

            sx = round(w / CANVAS_W, 6)
            sy = round(h / CANVAS_H, 6)
            xp = round((x_pos / CANVAS_W) * 100, 4)
            yp = round((y_pos / CANVAS_H) * 100, 4)
            wp = round(sx * 100, 4)
            hp = round(sy * 100, 4)

            screendef_xml = (
                f'<I N="{desc}_1" SX="{sx}" SY="{sy}" W="{w}" H="{h}" WP="{wp}" HP="{hp}" KA="true" Type="IDS">\n'
                f'          <En V="True" E=""/>\n'
                f'          <GUID>{guid}</GUID>\n'
                f'          <C V="01010100" E=""/>\n'
                f'          <IP X="{x_pos}" EX="" Y="{y_pos}" EY="" XP="{xp}" YP="{yp}"/>\n'
                f'          <ID V="{uid}" E=""/>\n'
                f'        </I>'
            )
            return {
                "library_xml": library_xml,
                "screendef_xml": screendef_xml,
                "dimensions_estimated": dimensions_estimated,
                "source": "LLM_QWEN_2.5_3B",
            }

        except Exception as exc:
            return {
                "library_xml": "",
                "screendef_xml": "",
                "dimensions_estimated": dimensions_estimated,
                "source": "LLM_ERROR",
                "error": str(exc),
            }


def compute_deterministic_checks(
    asset: dict,
    translation: dict,
    placed_boxes: list[tuple[float, float, float, float]],
) -> dict:
    """
    Extracts ground-truth boolean checks for Metric 1 (Tags), Metric 2 (Layout),
    and Metric 3 (Reversibility) to feed into Qwen 2.5 3B's boolean self-evaluation.
    """
    lib_str = translation.get("library_xml", "").strip()
    def_str = translation.get("screendef_xml", "").strip()
    src_el = asset["element"]
    dims_estimated = bool(translation.get("dimensions_estimated", False))

    checks = {
        # Metric 1: Tags
        "xml_well_formed": False,
        "whitelist_tags_only": False,
        "fc_enums_valid": True,
        "cross_section_refs_valid": False,
        "coordinate_math_valid": False,
        # Metric 2: Layout
        "within_800x480_bounds": False,
        "no_text_box_overflow": True,
        "aspect_ratio_and_no_overlap": False,
        "exact_bmp_dimensions_verified": not dims_estimated,
        # Metric 3: Reversibility
        "format_code_parity": True,
        "literal_multilang_parity": True,
        "image_anchor_transparency_parity": True,
        # Diagnostic details
        "diagnostics": [],
        "bbox": None,
    }

    if dims_estimated:
        checks["diagnostics"].append(
            "Source .bmp file not found locally; image Width/Height were estimated by AI and require verification."
        )

    try:
        lib_el = ET.fromstring(lib_str)
        def_el = ET.fromstring(def_str)
        checks["xml_well_formed"] = True
    except ET.ParseError as err:
        err_msg = translation.get("error") or f"XML parse error: {err}"
        checks["diagnostics"].append(err_msg)
        return checks

    # --- Metric 1: Tags ---
    lib_tags_ok = all(el.tag in VALID_LIBRARY_TAGS for el in lib_el.iter())
    def_tags_ok = all(el.tag in VALID_SCREENDEF_TAGS for el in def_el.iter())
    checks["whitelist_tags_only"] = lib_tags_ok and def_tags_ok
    if not checks["whitelist_tags_only"]:
        checks["diagnostics"].append("Unknown/unlisted XML tag detected in SRD output.")

    lib_id = (lib_el.findtext("ID") or "").strip()
    def_id_el = def_el.find("ID")
    def_id = (def_id_el.attrib.get("V", "") if def_id_el is not None else "").strip()
    inputs_el = lib_el.find("Inputs")
    fc_list = inputs_el.findall("FC") if inputs_el is not None else []
    dv_list = def_el.findall(".//DV")
    dv_indices_ok = all(0 <= int(dv.attrib.get("FC", -1)) < len(fc_list) for dv in dv_list) if dv_list else True
    checks["cross_section_refs_valid"] = (
        lib_id == str(asset["unified_id"])
        and def_id == str(asset["unified_id"])
        and dv_indices_ok
    )
    if not checks["cross_section_refs_valid"]:
        checks["diagnostics"].append("Cross-section ID or <DV FC> index mismatch between ScreenLibrary and ScreenDef.")

    base_map = {"0": "o", "1": "d", "2": "x", "3": "X"}
    prefix_map = {"0": "", "1": " ", "2": "+"}
    pad_map = {"0": "", "1": "", "2": "-", "3": "0"}
    reversed_tokens = []

    for fc in fc_list:
        t = fc.attrib.get("Type", "")
        p = fc.attrib.get("Prefix", "")
        pad = fc.attrib.get("Padding", "")
        d = fc.attrib.get("Digits", "")
        b = fc.attrib.get("Base", "")
        alt = fc.attrib.get("Alt", "")
        if t != "Signed" or p not in prefix_map or pad not in pad_map or b not in base_map or alt != "false" or not d.isdigit():
            checks["fc_enums_valid"] = False
            checks["diagnostics"].append(f"Invalid VBSE <FC> attribute values: {fc.attrib}")
        reversed_tokens.append(f"%{prefix_map.get(p, '?')}{pad_map.get(pad, '?')}{d}{base_map.get(b, '?')}")

    # --- Metric 1 (Coord Math) & Metric 2 (Layout) ---
    try:
        w = float(def_el.attrib.get("W", -1))
        h = float(def_el.attrib.get("H", -1))
        sx = float(def_el.attrib.get("SX", -1))
        sy = float(def_el.attrib.get("SY", -1))
        pos_el = def_el.find("SP") if def_el.tag == "Txt" else def_el.find("IP")
        x = float(pos_el.attrib.get("X", -1)) if pos_el is not None else -1
        y = float(pos_el.attrib.get("Y", -1)) if pos_el is not None else -1
        xp = float(pos_el.attrib.get("XP", -1)) if pos_el is not None else -1
        yp = float(pos_el.attrib.get("YP", -1)) if pos_el is not None else -1

        checks["coordinate_math_valid"] = (
            abs(sx - (w / CANVAS_W)) < 0.01
            and abs(sy - (h / CANVAS_H)) < 0.01
            and abs(xp - ((x / CANVAS_W) * 100)) < 0.1
            and abs(yp - ((y / CANVAS_H) * 100)) < 0.1
        )
        if not checks["coordinate_math_valid"]:
            checks["diagnostics"].append("Derived coordinate math (SX, SY, XP, YP) does not match pixel W, H, X, Y.")

        checks["within_800x480_bounds"] = (0 <= x and x + w <= CANVAS_W and 0 <= y and y + h <= CANVAS_H and w > 0 and h > 0)
        if not checks["within_800x480_bounds"]:
            checks["diagnostics"].append(f"Widget bounds ({x}, {y}, {w}x{h}) exceed 800x480 canvas.")

        overlaps = any(
            not (x + w <= px or px + pw <= x or y + h <= py or py + ph <= y)
            for (px, py, pw, ph) in placed_boxes
        )
        aspect_ok = True
        if def_el.tag == "I":
            lib_w = float(lib_el.findtext("Width", default="0"))
            lib_h = float(lib_el.findtext("Height", default="0"))
            if def_el.attrib.get("KA") == "true" and lib_w > 0 and lib_h > 0 and h > 0:
                aspect_ok = abs((w / h) - (lib_w / lib_h)) < 0.05
        checks["aspect_ratio_and_no_overlap"] = (not overlaps) and aspect_ok
        if overlaps:
            checks["diagnostics"].append("Widget overlaps with another element on the 800x480 screen.")
        if not aspect_ok:
            checks["diagnostics"].append("Image aspect ratio distorted despite KA='true'.")

        checks["bbox"] = (x, y, w, h)
    except ValueError:
        checks["diagnostics"].append("Non-numeric layout coordinates.")
        w = 0

    # --- Metric 2 (Text Overflow) & Metric 3 (Reversibility) ---
    if asset["kind"] == "Text":
        src_strings = {
            s.attrib.get("LanguageIndex", ""): s.attrib.get("TextData", "")
            for s in src_el.findall("String")
        }
        srd_strings = {
            s.attrib.get("LanguageIndex", ""): s.attrib.get("TextData", "")
            for s in lib_el.findall("String")
        }

        first_src = next(iter(src_strings.values()), "")
        src_printf_tokens = re.findall(r"%[+\- 0#]*\d*[dxXo]", first_src)
        checks["format_code_parity"] = (src_printf_tokens == reversed_tokens)
        if not checks["format_code_parity"]:
            checks["diagnostics"].append(
                f"Format code mismatch: source {src_printf_tokens} vs reversed <FC> {reversed_tokens}."
            )

        reconstructed_strings = {}
        max_expanded_chars = 0
        for lang_idx, srd_txt in srd_strings.items():
            rev_txt = srd_txt
            exp_txt = srd_txt
            for idx, tok in enumerate(reversed_tokens, start=1):
                rev_txt = rev_txt.replace(f"%{idx}$", tok)
                digits_match = re.findall(r"\d+", tok)
                width_chars = int(digits_match[0]) if digits_match else 4
                exp_txt = exp_txt.replace(f"%{idx}$", "0" * width_chars)
            reconstructed_strings[lang_idx] = rev_txt
            max_expanded_chars = max(max_expanded_chars, len(exp_txt))

        checks["literal_multilang_parity"] = (src_strings == reconstructed_strings) and len(src_strings) > 0
        if not checks["literal_multilang_parity"]:
            checks["diagnostics"].append("Multi-language literal string mismatch during round-trip check.")

        if w < max_expanded_chars * 11:
            checks["no_text_box_overflow"] = False
            checks["diagnostics"].append(
                f"Text box width W={int(w)}px is too narrow for {max_expanded_chars} chars (~{max_expanded_chars * 11}px)."
            )

    elif asset["kind"] == "Image":
        src_file = (src_el.findtext("SourceFileName") or "").strip()
        srd_file = (lib_el.findtext("SourceFileName") or "").strip()

        src_ip = src_el.find("InsertionPoint")
        srd_ip = lib_el.find("InsertionPoint")
        ip_ok = (
            src_ip is not None and srd_ip is not None
            and src_ip.attrib.get("X") == srd_ip.attrib.get("X")
            and src_ip.attrib.get("Y") == srd_ip.attrib.get("Y")
        )

        src_tr = src_el.find("Transparent")
        srd_tr = lib_el.find("Transparent")
        tr_ok = (
            src_tr is not None and srd_tr is not None
            and src_tr.attrib.get("X") == srd_tr.attrib.get("X")
            and src_tr.attrib.get("Y") == srd_tr.attrib.get("Y")
            and src_tr.attrib.get("LowLeft") == srd_tr.attrib.get("LowLeft")
        )

        checks["image_anchor_transparency_parity"] = (src_file == srd_file) and ip_ok and tr_ok
        if not checks["image_anchor_transparency_parity"]:
            checks["diagnostics"].append("Image SourceFileName, InsertionPoint, or Transparent coordinates shifted.")

    return checks


def evaluate_with_llm(asset: dict, translation: dict, placed_boxes: list[tuple[float, float, float, float]]) -> dict:
    """
    Runs Qwen 2.5 3B boolean self-evaluation across Tags, Layout, and Reversibility,
    then classifies into GREEN (3/3), YELLOW (2/3), or RED (<=1/3) with no retry loop.
    """
    checks = compute_deterministic_checks(asset, translation, placed_boxes)

    gt_tags = (
        checks["xml_well_formed"]
        and checks["whitelist_tags_only"]
        and checks["fc_enums_valid"]
        and checks["cross_section_refs_valid"]
        and checks["coordinate_math_valid"]
    )
    gt_layout = (
        checks["xml_well_formed"]
        and checks["within_800x480_bounds"]
        and checks["no_text_box_overflow"]
        and checks["aspect_ratio_and_no_overlap"]
        and checks["exact_bmp_dimensions_verified"]
    )
    gt_reversibility = (
        checks["xml_well_formed"]
        and checks["format_code_parity"]
        and checks["literal_multilang_parity"]
        and checks["image_anchor_transparency_parity"]
    )

    system_prompt = """You are a Danfoss DP600-to-DM710 migration validator. Grade the translation using THREE STRICT BOOLEAN METRICS (true or false only — no numeric scores):
1. "Tags": True ONLY if XML is well-formed, uses valid SRD tags & VBSE <FC> enums, cross-section IDs match, and coordinate math (SX, SY, XP, YP) is exact.
2. "Layout": True ONLY if widget fits inside 800x480 bounds, text width W avoids truncation, aspect ratio / non-overlap pass, and exact_bmp_dimensions_verified is true.
3. "Reversibility": True ONLY if reverse-compiled printf format codes, all multi-language strings, and image anchor/transparency coordinates match the DP600 source exactly.

Respond ONLY in valid JSON:
{"Tags": bool, "Layout": bool, "Reversibility": bool, "reason": "concise 1-sentence explanation"}"""

    user_prompt = f"""Evaluate this translation:
- Source DP600 XML:
{asset['raw_xml']}

- Generated SRD Library XML:
{translation.get('library_xml', '')}

- Generated SRD ScreenDef XML:
{translation.get('screendef_xml', '')}

- Verification Results:
{json.dumps(checks, indent=2)}"""

    try:
        llm_eval = call_ollama_json(system_prompt, user_prompt)
        llm_reason = llm_eval.get("reason", "").strip()
    except Exception as exc:
        llm_eval = {}
        llm_reason = f"LLM check fallback: {exc}"

    tags_bool = (llm_eval.get("Tags", gt_tags) is True) and gt_tags
    layout_bool = (llm_eval.get("Layout", gt_layout) is True) and gt_layout
    rev_bool = (llm_eval.get("Reversibility", gt_reversibility) is True) and gt_reversibility

    satisfied_count = int(tags_bool) + int(layout_bool) + int(rev_bool)
    failed_metrics = []
    if not tags_bool:
        failed_metrics.append("Tags")
    if not layout_bool:
        failed_metrics.append("Layout")
    if not rev_bool:
        failed_metrics.append("Reversibility")

    if satisfied_count == 3:
        category = "GREEN"
        explanation = llm_reason or "All 3 boolean checks (Tags, Layout, Reversibility) satisfied."
    elif satisfied_count == 2:
        category = "YELLOW"
        diag_note = "; ".join(checks["diagnostics"]) if checks["diagnostics"] else llm_reason
        explanation = f"2/3 metrics passed; unsure on [{failed_metrics[0]}]: {diag_note}"
    else:
        category = "RED"
        diag_note = "; ".join(checks["diagnostics"]) if checks["diagnostics"] else llm_reason
        explanation = f"Only {satisfied_count}/3 metrics passed; failed [{', '.join(failed_metrics)}]: {diag_note}"

    safe_explanation = explanation.replace("--", "-")

    return {
        "Tags": tags_bool,
        "Layout": layout_bool,
        "Reversibility": rev_bool,
        "satisfied_count": satisfied_count,
        "category": category,
        "explanation": safe_explanation,
        "bbox": checks["bbox"],
    }




def main() -> int:
    """Deterministic conversion followed by optional, reviewable AI proposals.

    Python 3.10+, standard library only. No original scripts are required.
    The SRD is an intermediate resource conversion, not a verified device project.
    AI proposals never overwrite verified source IDs, strings, or folder structure.
    """
    global MODEL_NAME, OLLAMA_URL
    parser = create_argument_parser()
    parser.add_argument("--llm", action="store_true", help="Generate and evaluate proposals for unresolved resources using local Ollama")
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--ollama-url", default=OLLAMA_URL, help="Ollama /api/chat endpoint")
    parser.add_argument("--audit", type=Path, help="JSON proposal/check report; defaults to OUTPUT.audit.json")
    args = parser.parse_args()
    MODEL_NAME, OLLAMA_URL = args.model, args.ollama_url
    manifest_path = args.manifest or default_manifest_path(args.output)
    audit_path = args.audit or args.output.with_suffix(".audit.json")
    try:
        outputs = [args.output, manifest_path, audit_path]
        if len({p.resolve() for p in outputs}) != len(outputs):
            raise ValueError("Output, manifest and audit paths must be different")
        if any(p.resolve() in {args.tds.resolve(), args.ids.resolve()} for p in outputs):
            raise ValueError("Output paths must not overwrite the inputs")
        if args.image_dir is not None and not args.image_dir.is_dir():
            raise ValueError(f"Image directory does not exist: {args.image_dir}")
        tree, manifest, warnings, offset = build_sdr(args.tds, args.ids, args.output, args.library_name, args.image_dir)
        assets, _ = parse_dp600_files(args.tds, args.ids)
        search_dirs = list(dict.fromkeys(p for p in [args.image_dir, args.ids.parent, args.tds.parent] if p is not None))
        report = {"llm_enabled": args.llm, "model": MODEL_NAME if args.llm else None,
                  "note": "AI translations and synthetic layouts are proposals only; unresolved tasks remain open. No real screen-layout source was supplied.", "resources": []}
        placed_boxes = []
        for idx, asset in enumerate(assets):
            tasks = [t for t in manifest["tasks"] if t["resource_type"] == asset["kind"] and t["resource_id"] == asset["unified_id"]]
            item = {"link_id": asset["link_id"], "kind": asset["kind"], "original_id": asset["original_id"], "output_id": asset["unified_id"], "description": asset["description"], "folder_path": asset["folder_path"], "task_ids": [t["task_id"] for t in tasks], "status": "needs_review" if tasks else "deterministic"}
            if args.llm and tasks:
                print(f"[{idx+1}/{len(assets)}] Proposing {asset['kind']} {asset['unified_id']}", flush=True)
                try:
                    translation = try_preset_rule(asset, slot_index=idx)
                    if translation is None:
                        translation = translate_asset_with_llm(asset, slot_index=idx, search_dirs=search_dirs)
                    evaluation = evaluate_with_llm(asset, translation, placed_boxes)
                    item.update(proposal=translation, evaluation=evaluation)
                    if evaluation["category"] in ("GREEN", "YELLOW") and evaluation["bbox"] is not None:
                        placed_boxes.append(evaluation["bbox"])
                except Exception as exc:
                    item["proposal_error"] = str(exc)
            report["resources"].append(item)
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
        tree.write(args.output, encoding="utf-8", xml_declaration=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
        audit_path.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
        print(f"SRD: {args.output}\nUnresolved tasks: {manifest_path}\nAudit/proposals: {audit_path}")
        print(f"Text IDs preserved; image ID offset: {offset}. Unresolved tasks: {len(manifest['tasks'])}")
        for warning in warnings:
            print(f"Warning: {warning}")
        return 0
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
