#!/usr/bin/env python3
"""Offline executable spec for the attribute matcher (match rung 3).

No API calls. These are synthetic minimal cases that pin the SCORER'S CONTRACT
-- gates, normalization, directional pressure, the margin rule, tie handling,
and the confidence cap -- independently of the LLM extraction step. They run in
milliseconds and lock the behavior the four sample RFQs exercise end to end.

Run: python scripts/verify_matcher_spec.py
"""

from __future__ import annotations

import sys
from decimal import Decimal

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import CATALOG_CSV
from rfq_agent.scoring import score_attribute_line
from rfq_agent.schemas import ExtractedLine, LineAttributes, MatchStatus, UOM

INDEX = load_catalog_index(CATALOG_CSV)
_FAILURES: list[str] = []


def mkline(conf: float = 1.0, **attrs) -> ExtractedLine:
    return ExtractedLine(
        line_no=1,
        source_text="synthetic spec case",
        sku=None,
        description=None,
        quantity=Decimal("1"),
        uom=UOM.ea,
        attributes=LineAttributes(**attrs),
        requested_delivery=None,
        notes=None,
        extraction_confidence=conf,
    )


def cand_skus(result) -> list[str]:
    return [c.sku for c in result.candidates]


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} -- {detail}")
        _FAILURES.append(name)


# -- Gate: wrong hose ID can never appear, even loosely -----------------------
def test_hose_id_gate() -> None:
    r = score_attribute_line(
        mkline(category_hint="hydraulic hose", hose_id_in="3/4",
               construction="SAE 100R2AT"),
        INDEX,
    )
    prods = [INDEX.get(s) for s in cand_skus(r)]
    only_34 = all(p is not None and p.hose_id_in == "3/4" for p in prods)
    check("hose_id gate: every candidate is 3/4 ID", only_34,
          f"candidates={cand_skus(r)}")
    check("hose_id gate: top match is SHF-H2-0750",
          r.matched_sku == "SHF-H2-0750", f"got {r.matched_sku}")


# -- Directional pressure: under-rated is disqualified, not down-ranked -------
def test_pressure_is_directional() -> None:
    r = score_attribute_line(
        mkline(category_hint="hydraulic hose", hose_id_in="3/4",
               working_pressure_psi=2000),
        INDEX,
    )
    # SHF-H1-0750 is rated 1800 < 2000 and must be excluded entirely.
    check("pressure gate: under-rated SHF-H1-0750 excluded",
          "SHF-H1-0750" not in cand_skus(r), f"candidates={cand_skus(r)}")
    every_meets = all(
        (p := INDEX.get(s)) and p.working_pressure_psi is not None
        and p.working_pressure_psi >= 2000
        for s in cand_skus(r)
    )
    check("pressure gate: every candidate meets >= 2000 psi", every_meets)

    # Above every 3/4 hose rating -> nothing qualifies -> no_match.
    r2 = score_attribute_line(
        mkline(category_hint="hydraulic hose", hose_id_in="3/4",
               working_pressure_psi=3200),
        INDEX,
    )
    check("pressure gate: request above all ratings -> no_match",
          r2.status == MatchStatus.no_match, f"got {r2.status}")


# -- Normalization: sparse match scores on what's specified, not penalized ----
def test_normalization_sparse_match() -> None:
    r = score_attribute_line(
        mkline(category_hint="clamp", hose_id_in="1", material="stainless"),
        INDEX,
    )
    check("normalization: 3-of-7-attribute clamp scores ~1.0",
          r.score is not None and r.score >= 0.99, f"score={r.score}")
    check("normalization: clamp is a confident attribute_match",
          r.status == MatchStatus.attribute_match and r.matched_sku == "SHF-CLP-100",
          f"status={r.status} sku={r.matched_sku}")


# -- Tie: equal candidates -> low_confidence + ALL shown, never a silent pick -
def test_tie_defers_to_human() -> None:
    r = score_attribute_line(
        mkline(category_hint="hose", hose_id_in="1/4"),  # generic -> ANY_HOSE
        INDEX,
    )
    check("tie: status is low_confidence", r.status == MatchStatus.low_confidence,
          f"got {r.status}")
    check("tie: flagged for human review", r.needs_human_review is True)
    check("tie: at least 2 candidates shown", len(r.candidates) >= 2,
          f"candidates={cand_skus(r)}")
    check("tie: PTFE option is not hidden from the reviewer",
          "SHF-PTFE-025" in cand_skus(r), f"candidates={cand_skus(r)}")


# -- Margin: a clear winner with margin >= 0.10 auto-accepts ------------------
def test_margin_rule() -> None:
    r = score_attribute_line(
        mkline(category_hint="fitting", hose_id_in="3/4",
               material="carbon steel", end_a="NPT male"),
        INDEX,
    )
    check("margin: clear winner is attribute_match (no review)",
          r.status == MatchStatus.attribute_match
          and r.matched_sku == "SHF-FIT-NPT-12M"
          and r.needs_human_review is False,
          f"status={r.status} sku={r.matched_sku}")
    check("margin: runner-up SHF-FIT-JIC-12M kept and scored lower",
          "SHF-FIT-JIC-12M" in cand_skus(r), f"candidates={cand_skus(r)}")
    all_fittings = all((p := INDEX.get(s)) and p.category == "Fitting"
                       for s in cand_skus(r))
    check("category gate: a fitting line returns only fittings", all_fittings)


# -- Confidence cap: a shaky extraction can never auto-accept -----------------
def test_confidence_cap() -> None:
    r = score_attribute_line(
        mkline(conf=0.4, category_hint="clamp", hose_id_in="1",
               material="stainless"),
        INDEX,
    )
    check("confidence cap: perfect score still capped to low_confidence",
          r.status == MatchStatus.low_confidence
          and r.score is not None and r.score >= 0.99
          and r.needs_human_review is True,
          f"status={r.status} score={r.score}")


# -- matched_sku semantics across statuses ------------------------------------
def test_matched_sku_semantics() -> None:
    empty = score_attribute_line(mkline(), INDEX)  # no scorable attributes
    check("empty line -> no_match with no matched_sku",
          empty.status == MatchStatus.no_match and empty.matched_sku is None,
          f"status={empty.status} sku={empty.matched_sku}")


def main() -> None:
    for test in (
        test_hose_id_gate,
        test_pressure_is_directional,
        test_normalization_sparse_match,
        test_tie_defers_to_human,
        test_margin_rule,
        test_confidence_cap,
        test_matched_sku_semantics,
    ):
        test()
    if _FAILURES:
        print(f"\n{len(_FAILURES)} matcher spec check(s) FAILED: {_FAILURES}")
        sys.exit(1)
    print("\nall matcher spec checks passed")


if __name__ == "__main__":
    main()
