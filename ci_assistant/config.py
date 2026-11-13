from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    products_file: Path = ROOT / "data" / "sample_products.json"
    docs_dir: Path = ROOT / "data" / "raw"
    llm_provider: str = "stub"  # "stub" | "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    top_k: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            products_file=ROOT / os.environ.get("CI_PRODUCTS_FILE", "data/sample_products.json"),
            docs_dir=ROOT / os.environ.get("CI_DOCS_DIR", "data/raw"),
            llm_provider=os.environ.get("CI_LLM_PROVIDER", "stub"),
            llm_model=os.environ.get("CI_LLM_MODEL", "claude-sonnet-5-5"),
            top_k=int(os.environ.get("CI_TOP_K", "5")),
        )
