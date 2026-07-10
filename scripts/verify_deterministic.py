#!/usr/bin/env python3
"""Verify deterministic core: ingest, SKU match rungs 1-2, enrich/pricing."""

from __future__ import annotations

from decimal import Decimal

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import CATALOG_CSV, PROJECT_ROOT, RFQS_DIR
from rfq_agent.nodes.enrich import enrich
from rfq_agent.nodes.ingest import ingest
from rfq_agent.nodes.match import match_lines
from rfq_agent.schemas import (
    ExtractedLine,
    ExtractedRFQ,
    LineAttributes,
    MatchStatus,
    UOM,
)

FIXTURES = PROJECT_ROOT / "fixtures" / "extracted"


def _pass(name: str) -> None:
    print(f"PASS: {name}")


def _fail(name: str, detail: str) -> None:
    print(f"FAIL: {name} -- {detail}")
    raise AssertionError(f"{name}: {detail}")


def check_ingest() -> None:
    name = "A ingest smoke (all 4 RFQs)"
    try:
        docs = {}
        for path in sorted(RFQS_DIR.iterdir()):
            if path.suffix.lower() not in {".pdf", ".eml"}:
                continue
            docs[path.name] = ingest(path)

        assert len(docs) == 4, f"expected 4 docs, got {len(docs)}"

        for pdf_name in (
            "RFQ-001_CarolinaFluidPower.pdf",
            "RFQ-003_PiedmontHydraulics.pdf",
        ):
            doc = docs[pdf_name]
            n = len(doc.raw_text)
            print(f"  PDF {pdf_name}: {n} chars")
            assert n > 50, f"{pdf_name} text too short: {n}"

        for eml_name in (
            "RFQ-002_GulfCoastIndustrial.eml",
            "RFQ-004_DeltaPower.eml",
        ):
            doc = docs[eml_name]
            print(
                f"  EML {eml_name}: sender={doc.sender!r} "
                f"subject={doc.subject!r} sent_date={doc.sent_date!r}"
            )
            assert doc.sender, f"{eml_name} missing sender"
            assert doc.subject, f"{eml_name} missing subject"
            assert doc.sent_date, f"{eml_name} missing ISO date"

        _pass(name)
    except Exception as exc:  # noqa: BLE001 -- surface as FAIL line
        _fail(name, str(exc))


def check_rfq001(index) -> None:
    name = "B RFQ-001 fixture match+enrich"
    try:
        extracted = ExtractedRFQ.model_validate_json(
            (FIXTURES / "RFQ-001.json").read_text(encoding="utf-8")
        )
        matches = match_lines(extracted, index)
        lines, subtotal = enrich(extracted, matches, index)

        assert len(lines) == 6
        for line in lines:
            assert line.match.status == MatchStatus.exact_sku
            assert line.match.needs_human_review is False
            assert line.flags == []

        expected_ext = [
            Decimal("4975.00"),
            Decimal("2430.00"),
            Decimal("230.00"),
            Decimal("190.00"),
            Decimal("185.00"),
            Decimal("1062.50"),
        ]
        actual_ext = [line.extended_price for line in lines]
        assert actual_ext == expected_ext, f"extended={actual_ext}"
        assert subtotal == Decimal("9072.50"), f"subtotal={subtotal}"
        print(f"  extended={actual_ext} subtotal={subtotal}")
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def check_rfq004(index) -> None:
    name = "C RFQ-004 fixture match+enrich"
    try:
        extracted = ExtractedRFQ.model_validate_json(
            (FIXTURES / "RFQ-004.json").read_text(encoding="utf-8")
        )
        matches = match_lines(extracted, index)
        lines, subtotal = enrich(extracted, matches, index)

        priced_ext = []
        for line in lines:
            if line.line_no == 5:
                assert line.match.status == MatchStatus.unknown_sku
                assert line.unit_price is None
                assert line.match.needs_human_review is True
                assert 1 <= len(line.match.candidates) <= 3
                assert any(
                    c.sku.startswith("SHF-H2-0") for c in line.match.candidates
                ), f"candidates={line.match.candidates}"
                assert "unknown_sku" in line.flags
            else:
                assert line.match.status == MatchStatus.exact_sku
                assert line.extended_price is not None
                priced_ext.append(line.extended_price)
                assert "deadline_risk" not in line.flags
                assert "stock_shortfall" not in line.flags

        expected_priced = [
            Decimal("2680.00"),
            Decimal("2670.00"),
            Decimal("250.00"),
            Decimal("166.00"),
            Decimal("1440.00"),
        ]
        assert priced_ext == expected_priced, f"priced_ext={priced_ext}"
        assert subtotal == Decimal("7206.00"), f"subtotal={subtotal}"
        print(
            f"  priced_ext={priced_ext} subtotal={subtotal} "
            f"line5_candidates={[c.sku for c in lines[4].match.candidates]}"
        )
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def check_rung3_path(index) -> None:
    name = "D sku=None routes to attribute scorer"
    try:
        extracted = ExtractedRFQ(
            lines=[
                ExtractedLine(
                    line_no=1,
                    source_text="attribute-only line",
                    sku=None,
                    quantity=Decimal("10"),
                    uom=UOM.ft,
                    attributes=LineAttributes(
                        category_hint="clamp",
                        hose_id_in="1 inch",
                        material="stainless",
                    ),
                    extraction_confidence=0.9,
                )
            ]
        )
        matches = match_lines(extracted, index)
        assert len(matches) == 1
        assert matches[0].status == MatchStatus.attribute_match
        assert matches[0].matched_sku == "SHF-CLP-100"
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def main() -> None:
    index = load_catalog_index(CATALOG_CSV)
    print(f"catalog loaded: {len(index.products)} SKUs")
    check_ingest()
    check_rfq001(index)
    check_rfq004(index)
    check_rung3_path(index)
    print("all deterministic checks passed")


if __name__ == "__main__":
    main()
