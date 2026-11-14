import json
import re
import struct
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

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


def read_bmp_dimensions(bmp_filename: str, search_dirs: list[Path]) -> tuple[int, int] | None:
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
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    return json.loads(response.json()["message"]["content"])


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
                    "unified_id": unified_id,
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
        bmp_dims = read_bmp_dimensions(src_filename, search_dirs)
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

    tags_bool = bool(llm_eval.get("Tags", gt_tags)) and gt_tags
    layout_bool = bool(llm_eval.get("Layout", gt_layout)) and gt_layout
    rev_bool = bool(llm_eval.get("Reversibility", gt_reversibility)) and gt_reversibility

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


def write_srd_and_comment_file(results: list[dict], lang_def_xml: str, srd_path: Path, comment_path: Path):
    """
    Writes SCREENREPO.SRD (keeping GREEN/YELLOW translations and copying raw original XML
    for RED sections) and outputs migration_comments.txt with bidirectional [LINK-ID] and
    exact SRD line-number links.
    """
    main_library_blocks = []
    sub_library_blocks = []
    screendef_blocks = []

    for item in results:
        asset = item["asset"]
        trans = item["translation"]
        ev = item["evaluation"]
        link_id = asset["link_id"]
        uid = asset["unified_id"]
        desc = asset["description"]
        cat = ev["category"]

        flag_tag = (
            f"[{link_id} | SRD_ID: {uid} | Asset: {desc} | Category: {cat} ({ev['satisfied_count']}/3) | "
            f"Tags={ev['Tags']}, Layout={ev['Layout']}, Reversibility={ev['Reversibility']}]"
        )

        if cat in ("GREEN", "YELLOW"):
            lib_entry = (
                f"      <!-- {flag_tag} {ev['explanation']} -->\n"
                f"      {trans['library_xml']}"
            )
            def_entry = (
                f"        <!-- {flag_tag} -->\n"
                f"        {trans['screendef_xml']}"
            )
            screendef_blocks.append(def_entry)
        else:
            lib_entry = (
                f"      <!-- {flag_tag} REJECTED - ORIGINAL DP600 XML COPIED BELOW: {ev['explanation']} -->\n"
                f"      {asset['raw_xml']}"
            )

        if len(asset["folder_path"]) > 1:
            sub_library_blocks.append(lib_entry)
        else:
            main_library_blocks.append(lib_entry)

    sub_folder_xml = ""
    if sub_library_blocks:
        sub_folder_xml = (
            '      <Folder Name="AnotherFolder">\n'
            + "\n".join(sub_library_blocks)
            + "\n      </Folder>\n"
        )

    srd_xml = f"""<?xml version="1.0"?>
<ScreenRepository>
  <FileFormat>006A</FileFormat>
  <ScreenLibrary Name="Library">
    <Folder Name="Main">
{sub_folder_xml}{chr(10).join(main_library_blocks)}
    </Folder>
{lang_def_xml}
  </ScreenLibrary>
  <ScreenDefRepository>
    <ScreenDef IDD="true" PT="false">
      <FF>003A</FF>
      <ULC X="0" EX="" Y="0" EY="" XP="0" YP="0"/>
      <W E="" V="{CANVAS_W}" VP="100"/>
      <H E="" V="{CANVAS_H}" VP="100"/>
      <D>ScreenDef</D>
      <GUID>C45215AC3_4998_46E2_94C8_6B9CF8355BDB</GUID>
      <C V="00FFFFFF" E=""/>
      <Interface>
        <Input>
          <Net Name="Voltage" Uid="1" DataType="S16"/>
          <Net Name="Color" Uid="2" DataType="COLOR"/>
        </Input>
        <Output/>
      </Interface>
      <PN>Display.Port</PN>
      <Lay LO="0" AR="true" LAR="false" KA="false"/>
      <F>
{chr(10).join(screendef_blocks)}
      </F>
      <PouCalls/>
    </ScreenDef>
  </ScreenDefRepository>
  <ApplogDefRepository/>
</ScreenRepository>
"""
    srd_path.write_text(srd_xml, encoding="utf-8")

    # Map each [LINK-ID] to its exact line number(s) in SCREENREPO.SRD
    srd_lines = srd_xml.splitlines()
    link_line_map: dict[str, list[int]] = {}
    for line_no, line_text in enumerate(srd_lines, start=1):
        match = re.search(r"\[(SEC-\d+)\s*\|", line_text)
        if match:
            link_line_map.setdefault(match.group(1), []).append(line_no)

    comment_lines = [
        "================================================================================",
        "DANFOSS DP600 -> DM710 MIGRATION AUDIT & LINKED COMMENT FILE",
        "Model: Qwen 2.5 3B (Ollama) | Evaluation: Boolean (Tags, Layout, Reversibility)",
        "Categories: GREEN (3/3) | YELLOW (2/3 - Uncertain) | RED (<=1/3 - Original XML)",
        "================================================================================\n",
    ]

    for item in results:
        asset = item["asset"]
        ev = item["evaluation"]
        link_id = asset["link_id"]
        lines_in_srd = link_line_map.get(link_id, [])
        line_ref = ", ".join(f"Line {ln}" for ln in lines_in_srd) if lines_in_srd else "Not found"

        comment_lines.append(
            f"[{link_id}] Category: {ev['category']} ({ev['satisfied_count']}/3) | "
            f"Asset: {asset['description']} ({asset['kind']}) | SRD Location: {line_ref}"
        )
        comment_lines.append(
            f"  Metrics -> Tags: {ev['Tags']} | Layout: {ev['Layout']} | Reversibility: {ev['Reversibility']}"
        )
        comment_lines.append(f"  Reason  -> {ev['explanation']}")
        if ev["category"] == "GREEN":
            comment_lines.append("  Status  -> Verified translation written to SCREENREPO.SRD.\n")
        elif ev["category"] == "YELLOW":
            comment_lines.append("  Status  -> Uncertain translation written to SCREENREPO.SRD; quick engineer check advised.\n")
        else:
            comment_lines.append("  Status  -> Unreliable translation discarded; original DP600 XML copied into SCREENREPO.SRD.\n")

    comment_path.write_text("\n".join(comment_lines), encoding="utf-8")


def main():
    base_dir = Path(__file__).resolve().parent

    tds_file = resolve_input_file(
        base_dir / "TEXTDATA.TDS",
        "Select DP600 Text XML (TEXTDATA.TDS)",
        [("TDS XML Files", "*.tds *.xml"), ("All Files", "*.*")],
    )
    ids_file = resolve_input_file(
        base_dir / "IMAGEDATA.IDS",
        "Select DP600 Image XML (IMAGEDATA.IDS)",
        [("IDS XML Files", "*.ids *.xml"), ("All Files", "*.*")],
    )

    srd_output = base_dir / "SCREENREPO.SRD"
    comment_output = base_dir / "migration_comments.txt"

    # Search both the input XML folder and the script folder for referenced .bmp files
    bmp_search_dirs = list(dict.fromkeys([ids_file.parent, tds_file.parent, base_dir]))

    print(f"Reading '{tds_file.name}' and '{ids_file.name}'...")
    assets, lang_def_xml = parse_dp600_files(tds_file, ids_file)

    results = []
    placed_boxes: list[tuple[float, float, float, float]] = []

    for idx, asset in enumerate(assets):
        print(f"[{idx + 1}/{len(assets)}] Processing {asset['link_id']} ({asset['kind']}: {asset['description']})...")

        # Step 1: Try preset rules first; fall back to local Qwen 2.5 3B
        translation = try_preset_rule(asset, slot_index=idx)
        if translation is None:
            translation = translate_asset_with_llm(asset, slot_index=idx, search_dirs=bmp_search_dirs)

        # Step 2: Single-pass boolean self-evaluation (Tags, Layout, Reversibility)
        evaluation = evaluate_with_llm(asset, translation, placed_boxes)
        if evaluation["category"] in ("GREEN", "YELLOW") and evaluation["bbox"] is not None:
            placed_boxes.append(evaluation["bbox"])

        print(
            f"   -> {evaluation['category']} ({evaluation['satisfied_count']}/3) | "
            f"Tags={evaluation['Tags']}, Layout={evaluation['Layout']}, Reversibility={evaluation['Reversibility']}"
        )
        results.append({
            "asset": asset,
            "translation": translation,
            "evaluation": evaluation,
        })

    write_srd_and_comment_file(results, lang_def_xml, srd_output, comment_output)
    print(f"\nCompleted! Outputs saved to:\n  - {srd_output}\n  - {comment_output}")


if __name__ == "__main__":
    main()