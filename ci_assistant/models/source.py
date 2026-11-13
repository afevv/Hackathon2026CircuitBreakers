from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRef:
    """Where a fact came from. Every extracted value should carry one."""

    url: str
    title: str = ""
    page: int | None = None
    snippet: str = ""

    def short(self) -> str:
        return f"{self.title or self.url}" + (f" p.{self.page}" if self.page else "")
