#!/usr/bin/env python3
"""Verify attribute scorer (rung 3) on RFQ-002/003 fixtures and confidence cap."""

from __future__ import annotations

from decimal import Decimal

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import (
    CATALOG_CSV,
    EXTRACTION_CONFIDENCE_FLOOR,
    MARGIN_REQUIRED,
    PROJECT_ROOT,
)
from rfq_agent.nodes.enrich import enrich
from rfq_agent.nodes.match import match_lines
from rfq_agent.schemas import (
    ExtractedLine,
    ExtractedRFQ,
    LineAttributes,
    MatchStatus,
    UOM,
)
from rfq_agent.scoring import score_attribute_line

FIXTURES = PROJECT_ROOT / "fixtures" / "extracted"


def _pass(name: str) -> None:
    print(f"PASS: {name}")


def _fail(name: str, detail: str) -> None:
    print(f"FAIL: {name} -- {detail}")
    raise AssertionError(f"{name}: {detail}")


def check_rfq002(index) -> None:
    name = "RFQ-002 match+enrich oracles"
    try:
        extracted = ExtractedRFQ.model_validate_json(
            (FIXTURES / "RFQ-002.json").read_text(encoding="utf-8")
        )
        matches = match_lines(extracted, index)
        lines, subtotal = enrich(extracted, matches, index)

        assert matches[0].status == MatchStatus.exact_sku
        assert matches[0].matched_sku == "SHF-H1-0500"
        assert matches[1].status == MatchStatus.exact_sku
        assert matches[1].matched_sku == "SHF-FIT-NPT-08M"

        assert matches[2].status == MatchStatus.attribute_match
        assert matches[2].matched_sku == "SHF-PN-0375"
        assert matches[2].score is not None and matches[2].score >= 0.99

        assert matches[3].status == MatchStatus.attribute_match
        assert matches[3].matched_sku == "SHF-FIT-NPT-12M"
        assert matches[3].score is not None and matches[3].score >= 0.99
        assert len(matches[3].candidates) >= 2
        runner = matches[3].candidates[1]
        assert runner.sku == "SHF-FIT-JIC-12M", f"runner={runner.sku}"
        assert 0.80 <= runner.score <= 0.87, f"runner.score={runner.score}"
        assert matches[3].score - runner.score >= MARGIN_REQUIRED

        assert subtotal == Decimal("2612.50"), f"subtotal={subtotal}"
        for line in lines:
            assert "stock_shortfall" not in line.flags
            assert "deadline_risk" not in line.flags

        print(
            f"  L3={matches[2].matched_sku}@{matches[2].score:.3f} "
            f"L4={matches[3].matched_sku}@{matches[3].score:.3f} "
            f"runner={runner.sku}@{runner.score:.3f} subtotal={subtotal}"
        )
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def check_rfq003(index) -> None:
    name = "RFQ-003 match+enrich oracles"
    try:
        extracted = ExtractedRFQ.model_validate_json(
            (FIXTURES / "RFQ-003.json").read_text(encoding="utf-8")
        )
        matches = match_lines(extracted, index)
        lines, subtotal = enrich(extracted, matches, index)

        expected = [
            ("SHF-H2-0750", MatchStatus.attribute_match),
            ("SHF-FIT-JIC-12M", MatchStatus.attribute_match),
            ("SHF-ASM-H2-050-48-JJ", MatchStatus.attribute_match),
            ("SHF-FIT-ORFS-08M", MatchStatus.attribute_match),
            ("SHF-CLP-100", MatchStatus.attribute_match),
        ]
        for i, (sku, status) in enumerate(expected):
            m = matches[i]
            assert m.status == status, f"L{i+1} status={m.status}"
            assert m.matched_sku == sku, f"L{i+1} sku={m.matched_sku}"
            assert m.score is not None and m.score >= 0.99, f"L{i+1} score={m.score}"
            assert m.needs_human_review is False

        # Length must discriminate 48" vs 36" assembly.
        assert matches[2].matched_sku == "SHF-ASM-H2-050-48-JJ"
        assert all(
            c.sku != "SHF-ASM-H2-050-36-JJ" or c.score < matches[2].score
            for c in matches[2].candidates
        )

        # L5 sole survivor after gates.
        assert len(matches[4].candidates) >= 1
        if len(matches[4].candidates) > 1:
            assert matches[4].score - matches[4].candidates[1].score >= MARGIN_REQUIRED

        m6 = matches[5]
        assert m6.status == MatchStatus.low_confidence
        assert m6.needs_human_review is True
        assert lines[5].unit_price is None
        assert "low_confidence" in lines[5].flags
        assert len(m6.candidates) >= 2
        assert all(abs(c.score - 1.0) < 1e-6 for c in m6.candidates)
        assert any(c.sku == "SHF-PTFE-025" for c in m6.candidates), m6.candidates

        assert subtotal == Decimal("6882.25"), f"subtotal={subtotal}"
        print(
            f"  L1-5={[m.matched_sku for m in matches[:5]]} "
            f"L6_cands={[c.sku for c in m6.candidates]} subtotal={subtotal}"
        )
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def check_confidence_cap(index) -> None:
    name = "confidence cap forces low_confidence"
    try:
        line = ExtractedLine(
            line_no=1,
            source_text="Hose clamp, 1 inch, stainless T-bolt style",
            sku=None,
            quantity=Decimal("10"),
            uom=UOM.ea,
            attributes=LineAttributes(
                category_hint="clamp",
                hose_id_in="1 inch",
                material="stainless",
            ),
            extraction_confidence=0.4,
        )
        result = score_attribute_line(line, index)
        assert result.status == MatchStatus.low_confidence, result.status
        assert result.matched_sku == "SHF-CLP-100"
        assert result.score is not None and result.score >= 0.99
        assert result.needs_human_review is True
        assert "confidence" in result.rationale.lower()
        assert str(EXTRACTION_CONFIDENCE_FLOOR) in result.rationale or "0.60" in result.rationale
        print(f"  status={result.status} score={result.score} rationale={result.rationale!r}")
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def main() -> None:
    index = load_catalog_index(CATALOG_CSV)
    print(f"catalog loaded: {len(index.products)} SKUs")
    check_rfq002(index)
    check_rfq003(index)
    check_confidence_cap(index)
    print("all matching checks passed")


if __name__ == "__main__":
    main()
