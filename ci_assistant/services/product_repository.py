from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ci_assistant.models import Product


class JsonProductRepository:
    """Loads normalized products from a JSON file.

    Swap for a SQL / Databricks-backed repository by implementing the same
    ProductRepository methods.
    """

    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._products = [Product.from_dict(p) for p in data["products"]]
        self._by_id = {p.id: p for p in self._products}

    def list_all(self) -> list[Product]:
        return list(self._products)

    def list_danfoss(self) -> list[Product]:
        return [p for p in self._products if p.is_danfoss]

    def list_competitors(self) -> list[Product]:
        return [p for p in self._products if not p.is_danfoss]

    def get(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def to_dataframe(self) -> pd.DataFrame:
        """One row per product, one column per canonical attribute. Handy for Pandas work."""
        rows = []
        for p in self._products:
            row = {"id": p.id, "manufacturer": p.manufacturer, "model_code": p.model_code, "family": p.family}
            row.update({k: v.value for k, v in p.attributes.items()})
            rows.append(row)
        return pd.DataFrame(rows)
