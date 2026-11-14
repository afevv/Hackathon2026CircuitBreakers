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


def main() -> int:
    """
    Program entry point.
    """

    argument_parser = create_argument_parser()
    arguments = argument_parser.parse_args()

    manifest_path = (
        arguments.manifest
        if arguments.manifest is not None
        else default_manifest_path(
            arguments.output
        )
    )

    try:
        if (
            arguments.image_dir is not None
            and not arguments.image_dir.is_dir()
        ):
            raise ValueError(
                f"Image directory does not exist: "
                f"{arguments.image_dir}"
            )

        (
            output_tree,
            manifest,
            warnings,
            image_id_offset,
        ) = build_sdr(
            tds_path=arguments.tds,
            ids_path=arguments.ids,
            output_path=arguments.output,
            library_name=arguments.library_name,
            image_directory=arguments.image_dir,
        )

        output_tree.write(
            arguments.output,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=True,
        )

        with manifest_path.open(
            "w",
            encoding="utf-8",
        ) as manifest_file:
            json.dump(
                manifest,
                manifest_file,
                indent=2,
                ensure_ascii=False,
            )

            manifest_file.write("\n")

    except (
        FileNotFoundError,
        PermissionError,
        OSError,
        ValueError,
    ) as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )

        return 1

    print(f"TDS input:        {arguments.tds}")
    print(f"IDS input:        {arguments.ids}")
    print(f"SDR output:       {arguments.output}")
    print(f"Task manifest:    {manifest_path}")
    print(f"Library name:     {arguments.library_name}")
    print(f"Image ID offset:  {image_id_offset}")
    print(
        f"Unresolved tasks: "
        f"{len(manifest['tasks'])}"
    )

    if warnings:
        print("\nWarnings:")

        for warning in warnings:
            print(f"- {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())