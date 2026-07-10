"""Load and index product_catalog.csv."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from rfq_agent.normalize import normalize_sku
from rfq_agent.schemas import Product, UOM


@dataclass(frozen=True)
class CatalogIndex:
    """SKU lookup map plus full product list for later attribute scoring."""

    by_sku: dict[str, Product]
    products: list[Product]

    def get(self, sku: str) -> Product | None:
        return self.by_sku.get(normalize_sku(sku))


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _parse_optional_int(value: str | None) -> int | None:
    text = _empty_to_none(value)
    return int(text) if text is not None else None


def _parse_optional_decimal(value: str | None) -> Decimal | None:
    text = _empty_to_none(value)
    return Decimal(text) if text is not None else None


def load_catalog(path: Path | str) -> list[Product]:
    """Parse the product catalog CSV into typed Product rows."""
    products: list[Product] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            products.append(
                Product(
                    sku=row["sku"].strip(),
                    description=row["description"].strip(),
                    category=row["category"].strip(),
                    hose_id_in=_empty_to_none(row.get("hose_id_in")),
                    construction=_empty_to_none(row.get("construction")),
                    working_pressure_psi=_parse_optional_int(
                        row.get("working_pressure_psi")
                    ),
                    material=_empty_to_none(row.get("material")),
                    length_ft=_parse_optional_decimal(row.get("length_ft")),
                    end_a=_empty_to_none(row.get("end_a")),
                    end_b=_empty_to_none(row.get("end_b")),
                    unit_price_usd=Decimal(row["unit_price_usd"].strip()),
                    uom=UOM(row["uom"].strip()),
                    lead_time_days=int(row["lead_time_days"].strip()),
                    stock_qty=int(row["stock_qty"].strip()),
                )
            )
    return products


def build_catalog_index(products: list[Product]) -> CatalogIndex:
    """Index products by normalized SKU (uppercase, stripped; dashes kept)."""
    by_sku = {normalize_sku(product.sku): product for product in products}
    return CatalogIndex(by_sku=by_sku, products=products)


def load_catalog_index(path: Path | str) -> CatalogIndex:
    """Load CSV and return a CatalogIndex."""
    return build_catalog_index(load_catalog(path))
