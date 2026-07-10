"""Enrich node: Decimal pricing, lead time, stock, deadline flags."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from rfq_agent.catalog.loader import CatalogIndex
from rfq_agent.normalize import normalize_sku
from rfq_agent.schemas import (
    ExtractedRFQ,
    MatchResult,
    MatchStatus,
    QuoteLine,
)

TWOPLACES = Decimal("0.01")
PRICED_STATUSES = {MatchStatus.exact_sku, MatchStatus.attribute_match}


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def enrich(
    extracted: ExtractedRFQ,
    matches: list[MatchResult],
    catalog: CatalogIndex,
) -> tuple[list[QuoteLine], Decimal]:
    """Attach catalog commercial fields and compute extended prices / subtotal."""
    if len(matches) != len(extracted.lines):
        raise ValueError("matches length must equal extracted.lines length")

    rfq_date = _parse_iso_date(extracted.rfq_date)
    needed_by = _parse_iso_date(extracted.needed_by)
    quote_lines: list[QuoteLine] = []
    subtotal = Decimal("0.00")

    for line, match in zip(extracted.lines, matches, strict=True):
        flags: list[str] = []
        catalog_description = None
        unit_price = None
        uom = None
        lead_time_days = None
        stock_qty = None
        extended_price = None

        if match.status in PRICED_STATUSES and match.matched_sku:
            product = catalog.by_sku[normalize_sku(match.matched_sku)]
            catalog_description = product.description
            unit_price = product.unit_price_usd
            uom = product.uom
            lead_time_days = product.lead_time_days
            stock_qty = product.stock_qty
            extended_price = _quantize_money(line.quantity * unit_price)
            subtotal += extended_price

            if line.quantity > Decimal(stock_qty):
                flags.append("stock_shortfall")
            if (
                rfq_date is not None
                and needed_by is not None
                and (needed_by - rfq_date).days < lead_time_days
            ):
                flags.append("deadline_risk")
        else:
            flags.append(match.status.value)

        quote_lines.append(
            QuoteLine(
                line_no=line.line_no,
                source_text=line.source_text,
                extracted=line,
                match=match,
                catalog_description=catalog_description,
                unit_price=unit_price,
                uom=uom,
                lead_time_days=lead_time_days,
                stock_qty=stock_qty,
                extended_price=extended_price,
                flags=flags,
            )
        )

    return quote_lines, _quantize_money(subtotal)
