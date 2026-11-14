from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LegacyTextEntry(BaseModel):
    id: str
    line_id: str = ""
    description: str = ""
    legacy_format: str = ""
    source_file: str = "TEXTDATA.TDS"
    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    ocr_text: Optional[str] = None
    can_signal: Optional[str] = None
    polarity: str = "NORMAL"


class LegacyImageAsset(BaseModel):
    id: str
    line_id: str = ""
    source_file: str = "IMAGEDATA.IDS"
    file_name: str
    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    color_depth: int = 1
    output_format: str = "RLE"
    can_signal: Optional[str] = None
    polarity: str = "NORMAL"


class UIRDocument(BaseModel):
    text_entries: List[LegacyTextEntry] = Field(default_factory=list)
    image_entries: List[LegacyImageAsset] = Field(default_factory=list)
    source_files: List[str] = Field(default_factory=lambda: ["TEXTDATA.TDS", "IMAGEDATA.IDS"])


class AspectRatio(BaseModel):
    width: int = Field(gt=0, le=1000)
    height: int = Field(gt=0, le=1000)


class MigrationRequest(BaseModel):
    scenario: Literal["demo", "hazard"] = "demo"
    use_local_ollama: bool = False
    screen_image_base64: Optional[str] = None
    asset_images: Dict[str, str] = Field(default_factory=dict)
    aspect_ratio_overrides: Dict[str, AspectRatio] = Field(default_factory=dict)


class LineFlag(BaseModel):
    line_id: str
    resource_id: str
    resource_type: Literal["text", "image"]
    source_file: str
    legacy_match: str
    can_signal: Optional[str] = None
    polarity: str
    route: str
    rule_id: Optional[str] = None
    valid: bool
    flag: Literal["PASS", "REVIEW"]
    checks: Dict[str, bool]


class MigrationResult(BaseModel):
    scenario: str
    validation: bool
    srd_xml: str
    dictionary_snapshot: Dict[str, Any]
    aspect_ratio_overrides: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    translation_trace: List[Dict[str, Any]] = Field(default_factory=list)
    line_flags: List[LineFlag] = Field(default_factory=list)
    line_flag_audit_report: str = ""
    upscaled_assets: Dict[str, str] = Field(default_factory=dict)
    safety_veto: bool = False
    unresolved: int = 0
