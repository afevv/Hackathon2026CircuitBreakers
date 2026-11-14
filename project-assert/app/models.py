from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LegacyTextEntry(BaseModel):
    id: str
    description: str = ""
    legacy_format: str = ""
    source_file: str = "TEXTDATA.TDS"
    can_signal: Optional[str] = None
    polarity: str = "NORMAL"


class LegacyImageAsset(BaseModel):
    id: str
    source_file: str = "IMAGEDATA.IDS"
    file_name: str
    x: int
    y: int
    width: int
    height: int
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
    aspect_ratio_overrides: Dict[str, AspectRatio] = Field(default_factory=dict)


class ScoreMetrics(BaseModel):
    syntax: float = 0.0
    safety: float = 0.0
    layout: float = 0.0
    traceability: float = 0.0
    total: float = 0.0
    safety_veto: bool = False
    unresolved: int = 0


class MigrationResult(BaseModel):
    scenario: str
    tier: str
    iterations: int
    score: float
    srd_xml: str
    dictionary_snapshot: Dict[str, Any]
    aspect_ratio_overrides: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    translation_trace: List[Dict[str, Any]] = Field(default_factory=list)
    safety_veto: bool = False
    unresolved: int = 0
