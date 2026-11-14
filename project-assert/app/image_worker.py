from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image, UnidentifiedImageError
from xml.etree import ElementTree as ET

from app.models import UIRDocument


SAMPLE_ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "sample-images"


def upscale_assets(
    document: UIRDocument,
    nodes_by_line: Dict[str, ET.Element],
    uploaded_assets: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    uploaded_by_name = {
        Path(name).name.casefold(): encoded for name, encoded in uploaded_assets.items()
    }
    upscaled: Dict[str, str] = {}
    filenames_by_line: Dict[str, str] = {}

    for asset in document.image_entries:
        node = nodes_by_line.get(asset.line_id)
        if node is None or node.get("Width") is None or node.get("Height") is None:
            continue

        encoded = uploaded_by_name.get(Path(asset.file_name).name.casefold())
        if encoded is None:
            sample_path = SAMPLE_ASSET_DIR / Path(asset.file_name).name
            if not sample_path.is_file():
                continue
            source_bytes = sample_path.read_bytes()
        else:
            try:
                source_bytes = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError(f"Uploaded asset {asset.file_name} is not valid base64.") from exc

        try:
            target_size = (int(node.get("Width")), int(node.get("Height")))
            if target_size[0] <= 0 or target_size[1] <= 0:
                continue
            with Image.open(BytesIO(source_bytes)) as source_image:
                resized = source_image.convert("RGBA").resize(target_size, Image.Resampling.LANCZOS)
                output = BytesIO()
                resized.save(output, format="PNG")
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ValueError(f"Could not upscale image asset {asset.file_name}: {exc}") from exc

        filename = f"{Path(asset.file_name).stem}_DM710.png"
        upscaled[filename] = base64.b64encode(output.getvalue()).decode("ascii")
        filenames_by_line[asset.line_id] = filename

    return upscaled, filenames_by_line
