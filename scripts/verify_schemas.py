#!/usr/bin/env python3
"""Verify schemas: Decimal JSON round-trip and strict validation."""

from __future__ import annotations

import json
from decimal import Decimal

from pydantic import ValidationError

from rfq_agent.schemas import (
    ExtractedLine,
    LineAttributes,
    LineOverride,
    MatchCandidate,
    MatchResult,
    MatchStatus,
    PackageStatus,
    QuoteLine,
    QuotePackage,
    UOM,
)


def build_sample_package() -> QuotePackage:
    priced = ExtractedLine(
        line_no=1,
        source_text='SHF-H2-0500 Hydraulic Hose SAE 100R2AT 1/2" ID 500 ft',
        sku="SHF-H2-0500",
        description='Hydraulic Hose SAE 100R2AT 1/2" ID',
        quantity=Decimal("500"),
        uom=UOM.ft,
        attributes=LineAttributes(hose_id_in="1/2", construction="SAE 100R2AT"),
        extraction_confidence=1.0,
    )
    unknown = ExtractedLine(
        line_no=2,
        source_text="SHF-H2-0625 300 ft",
        sku="SHF-H2-0625",
        description=None,
        quantity=Decimal("300"),
        uom=UOM.ft,
        attributes=LineAttributes(),
        extraction_confidence=0.95,
    )
    line1 = QuoteLine(
        line_no=1,
        source_text=priced.source_text,
        extracted=priced,
        match=MatchResult(
            status=MatchStatus.exact_sku,
            matched_sku="SHF-H2-0500",
            score=None,
            rationale="SKU provided on RFQ, found in catalog",
            candidates=[],
            needs_human_review=False,
        ),
        catalog_description='Hydraulic Hose SAE 100R2AT 1/2" ID',
        unit_price=Decimal("9.95"),
        uom=UOM.ft,
        lead_time_days=3,
        stock_qty=1600,
        extended_price=Decimal("4975.00"),
        flags=[],
    )
    line2 = QuoteLine(
        line_no=2,
        source_text=unknown.source_text,
        extracted=unknown,
        match=MatchResult(
            status=MatchStatus.unknown_sku,
            matched_sku=None,
            score=None,
            rationale="SKU SHF-H2-0625 is not in the catalog; not substituted",
            candidates=[
                MatchCandidate(
                    sku="SHF-H2-0500",
                    score=0.86,
                    breakdown={"edit_distance": "1 (nearest catalog SKU hint)"},
                    note="Reviewer hint only — not auto-priced",
                )
            ],
            needs_human_review=True,
        ),
        catalog_description=None,
        unit_price=None,
        uom=UOM.ft,
        lead_time_days=None,
        stock_qty=None,
        extended_price=None,
        flags=["unknown_sku"],
    )
    return QuotePackage(
        run_id="run-verify-schemas",
        quote_id="Q-DRAFT-VERIFY",
        status=PackageStatus.pending_review,
        customer_name="Delta Power Systems",
        contact_email="procurement@deltapowersystems.net",
        rfq_date="2026-04-08",
        needed_by="2026-04-30",
        ship_to="2240 Industrial Blvd, Jacksonville, FL 32218",
        terms="Net 30",
        lines=[line1, line2],
        subtotal=Decimal("4975.00"),
        flag_summary=["Line 2: unknown SKU SHF-H2-0625 — needs your decision"],
        email_to="procurement@deltapowersystems.net",
        email_subject="Quote Q-DRAFT-VERIFY",
        email_body="Please find pricing for line 1. Line 2 needs clarification.",
        approved=False,
        overrides=[
            LineOverride(
                line_no=2,
                action="replace_sku",
                replacement_sku="SHF-H2-0750",
                note="Customer confirmed 3/4 inch instead of 5/8",
            )
        ],
    )


def main() -> None:
    package = build_sample_package()

    raw = package.model_dump_json()
    reloaded = QuotePackage.model_validate(json.loads(raw))

    assert reloaded == package, "round-trip object equality failed"
    assert reloaded.subtotal == Decimal("4975.00"), f"subtotal value {reloaded.subtotal!r}"
    assert type(reloaded.subtotal) is Decimal, f"subtotal type {type(reloaded.subtotal)}"
    assert type(reloaded.lines[0].unit_price) is Decimal
    assert type(reloaded.lines[0].extended_price) is Decimal
    assert reloaded.lines[1].unit_price is None
    print("round-trip OK:", reloaded.subtotal, type(reloaded.subtotal).__name__)

    # Misspelled field must fail (extra=forbid).
    bad_extra = json.loads(raw)
    bad_extra["aproved"] = True
    raised_extra = False
    try:
        QuotePackage.model_validate(bad_extra)
    except ValidationError:
        raised_extra = True
    assert raised_extra, "expected ValidationError for misspelled field 'aproved'"

    # quantity: 0 must fail.
    raised_qty = False
    try:
        ExtractedLine(
            line_no=99,
            source_text="bad",
            quantity=Decimal("0"),
            attributes=LineAttributes(),
            extraction_confidence=1.0,
        )
    except ValidationError:
        raised_qty = True
    assert raised_qty, "expected ValidationError for quantity 0"

    print("strictness OK")
    print("all schema checks passed")


if __name__ == "__main__":
    main()
