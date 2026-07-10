#!/usr/bin/env python3
"""Verify LLM extraction reproduces fixture-verified pipeline totals on all 4 RFQs."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import CATALOG_CSV, RFQS_DIR, RUNS_DIR
from rfq_agent.nodes.enrich import enrich
from rfq_agent.nodes.extract import extract
from rfq_agent.nodes.ingest import ingest
from rfq_agent.nodes.match import match_lines
from rfq_agent.schemas import ExtractedRFQ, MatchStatus, RFQDocument


def _print_lines(extracted: ExtractedRFQ, out_path: Path) -> None:
    print(f"  extracted.json -> {out_path}")
    for line in extracted.lines:
        print(
            f"  L{line.line_no}: sku={line.sku!r} qty={line.quantity} "
            f"uom={line.uom} conf={line.extraction_confidence} "
            f"attrs={line.attributes.model_dump()}"
        )


def _run_pipeline(path: Path):
    document = ingest(path)
    extracted = extract(document)
    index = load_catalog_index(CATALOG_CSV)
    matches = match_lines(extracted, index)
    lines, subtotal = enrich(extracted, matches, index)
    out_path = RUNS_DIR / document.run_id / "extracted.json"
    return document, extracted, matches, lines, subtotal, out_path


def _assert_rfq001(extracted, matches, lines, subtotal) -> None:
    assert len(extracted.lines) == 6, f"line count={len(extracted.lines)}"
    skus = [ln.sku for ln in extracted.lines]
    qtys = [ln.quantity for ln in extracted.lines]
    assert skus == [
        "SHF-H2-0500",
        "SHF-H2-0375",
        "SHF-FIT-JIC-08M",
        "SHF-FIT-JIC-06M",
        "SHF-CLP-050",
        "SHF-ASM-H2-050-36-JJ",
    ], f"skus={skus}"
    assert qtys == [
        Decimal("500"),
        Decimal("300"),
        Decimal("50"),
        Decimal("50"),
        Decimal("100"),
        Decimal("25"),
    ], f"qtys={qtys}"
    assert extracted.rfq_date == "2026-04-07", extracted.rfq_date
    assert extracted.needed_by == "2026-04-21", extracted.needed_by
    assert all(m.status == MatchStatus.exact_sku for m in matches)
    assert subtotal == Decimal("9072.50"), f"subtotal={subtotal}"


def _assert_rfq002(extracted, matches, lines, subtotal) -> None:
    assert len(extracted.lines) == 4, f"line count={len(extracted.lines)}"
    assert extracted.lines[0].sku == "SHF-H1-0500"
    assert extracted.lines[0].quantity == Decimal("250")
    assert extracted.lines[1].sku == "SHF-FIT-NPT-08M"
    assert extracted.lines[1].quantity == Decimal("40")
    assert extracted.lines[2].sku is None
    assert extracted.lines[2].quantity == Decimal("200")
    assert extracted.lines[3].sku is None
    assert extracted.lines[3].quantity == Decimal("30")
    assert extracted.needed_by == "2026-04-25", extracted.needed_by
    assert matches[2].status == MatchStatus.attribute_match
    assert matches[2].matched_sku == "SHF-PN-0375"
    assert matches[3].status == MatchStatus.attribute_match
    assert matches[3].matched_sku == "SHF-FIT-NPT-12M"
    assert subtotal == Decimal("2612.50"), f"subtotal={subtotal}"


def _assert_rfq003(extracted, matches, lines, subtotal) -> None:
    assert len(extracted.lines) == 6, f"line count={len(extracted.lines)}"
    assert all(ln.sku is None for ln in extracted.lines), [
        ln.sku for ln in extracted.lines
    ]
    qtys = [ln.quantity for ln in extracted.lines]
    assert qtys == [
        Decimal("400"),
        Decimal("60"),
        Decimal("15"),
        Decimal("40"),
        Decimal("75"),
        Decimal("150"),
    ], f"qtys={qtys}"
    assert extracted.needed_by is None, f"needed_by={extracted.needed_by}"
    expected = [
        "SHF-H2-0750",
        "SHF-FIT-JIC-12M",
        "SHF-ASM-H2-050-48-JJ",
        "SHF-FIT-ORFS-08M",
        "SHF-CLP-100",
    ]
    for i, sku in enumerate(expected):
        assert matches[i].status == MatchStatus.attribute_match, (
            f"L{i+1} status={matches[i].status} rationale={matches[i].rationale}"
        )
        assert matches[i].matched_sku == sku, (
            f"L{i+1} matched={matches[i].matched_sku}"
        )
    assert matches[5].needs_human_review is True
    assert lines[5].unit_price is None
    assert matches[5].status in {
        MatchStatus.low_confidence,
        MatchStatus.no_match,
    }, matches[5].status
    assert subtotal == Decimal("6882.25"), f"subtotal={subtotal}"


def _assert_rfq004(extracted, matches, lines, subtotal) -> None:
    assert len(extracted.lines) == 6, f"line count={len(extracted.lines)}"
    assert extracted.lines[4].sku == "SHF-H2-0625"
    assert extracted.lines[4].quantity == Decimal("300")
    assert extracted.needed_by == "2026-04-30", extracted.needed_by
    for i, m in enumerate(matches):
        if i == 4:
            assert m.status == MatchStatus.unknown_sku, m.status
            assert m.candidates, "expected nearest-SKU candidates"
        else:
            assert m.status == MatchStatus.exact_sku, f"L{i+1} {m.status}"
    assert subtotal == Decimal("7206.00"), f"subtotal={subtotal}"


CHECKS = {
    "RFQ-001_CarolinaFluidPower.pdf": _assert_rfq001,
    "RFQ-002_GulfCoastIndustrial.eml": _assert_rfq002,
    "RFQ-003_PiedmontHydraulics.pdf": _assert_rfq003,
    "RFQ-004_DeltaPower.eml": _assert_rfq004,
}


def _check_one(filename: str) -> None:
    path = RFQS_DIR / filename
    assert_fn = CHECKS[filename]
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            print(f"\n=== {filename} (attempt {attempt + 1}) ===")
            _doc, extracted, matches, lines, subtotal, out_path = _run_pipeline(path)
            _print_lines(extracted, out_path)
            assert_fn(extracted, matches, lines, subtotal)
            print(f"PASS: {filename} subtotal={subtotal}")
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"FAIL attempt {attempt + 1}: {exc}")
            if attempt == 0:
                print("  retrying once (temperature=0 flakiness protocol)...")
    raise AssertionError(f"{filename} failed twice: {last_exc}")


def main() -> None:
    for filename in sorted(CHECKS):
        _check_one(filename)
    print("\nall extraction checks passed")


if __name__ == "__main__":
    main()
