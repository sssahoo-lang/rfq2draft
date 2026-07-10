#!/usr/bin/env python3
"""Verify assembly: quote package, review.md, guarded email on all 4 RFQs."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import CATALOG_CSV, RFQS_DIR, RUNS_DIR
from rfq_agent.nodes.assemble import assemble, extract_dollar_amounts, numeric_guard_ok
from rfq_agent.nodes.enrich import enrich
from rfq_agent.nodes.extract import extract
from rfq_agent.nodes.ingest import ingest
from rfq_agent.nodes.match import match_lines
from rfq_agent.schemas import QuotePackage

EXPECTED = {
    "RFQ-001_CarolinaFluidPower.pdf": {
        "subtotal": Decimal("9072.50"),
        "flagged": 0,
        "email_to": "purchasing@carolinafluidpower.com",
        "body_must_mention": None,
    },
    "RFQ-002_GulfCoastIndustrial.eml": {
        "subtotal": Decimal("2612.50"),
        "flagged": 0,
        "email_to": "jamie.ortiz@gulfcoastind.com",
        "body_must_mention": None,
    },
    "RFQ-003_PiedmontHydraulics.pdf": {
        "subtotal": Decimal("6882.25"),
        "flagged": 1,
        "flagged_line": 6,
        "email_to": "orders@piedmonthyd.com",
        "body_must_mention": "line 6",
    },
    "RFQ-004_DeltaPower.eml": {
        "subtotal": Decimal("7206.00"),
        "flagged": 1,
        "flagged_line": 5,
        "email_to": "procurement@deltapowersystems.net",
        "body_must_mention": "SHF-H2-0625",
    },
}


def _flagged_lines(package: QuotePackage) -> list:
    return [
        ln
        for ln in package.lines
        if ln.match.needs_human_review or ln.unit_price is None
    ]


def _run_one(filename: str) -> QuotePackage:
    path = RFQS_DIR / filename
    document = ingest(path)
    extracted = extract(document)
    index = load_catalog_index(CATALOG_CSV)
    matches = match_lines(extracted, index)
    quote_lines, subtotal = enrich(extracted, matches, index)
    return assemble(document, extracted, quote_lines, subtotal, index)


def _assert_package(filename: str, package: QuotePackage) -> None:
    exp = EXPECTED[filename]
    run_dir = RUNS_DIR / package.run_id
    pkg_path = run_dir / "quote_package.json"
    review_path = run_dir / "review.md"

    assert pkg_path.exists(), f"missing {pkg_path}"
    reloaded = QuotePackage.model_validate_json(pkg_path.read_text(encoding="utf-8"))
    assert reloaded.subtotal == exp["subtotal"], reloaded.subtotal
    assert package.subtotal == exp["subtotal"], package.subtotal

    flagged = _flagged_lines(package)
    assert len(flagged) == exp["flagged"], (
        f"flagged count={len(flagged)} lines={[ln.line_no for ln in flagged]}"
    )
    if exp["flagged"] == 1:
        assert flagged[0].line_no == exp["flagged_line"], flagged[0].line_no

    assert package.status.value == "pending_review"
    assert package.approved is False
    assert package.email_to == exp["email_to"], package.email_to

    body = package.email_body or ""
    ok, detail = numeric_guard_ok(body, package.subtotal)
    assert ok, detail
    amounts = extract_dollar_amounts(body)
    for amount in amounts:
        assert amount == package.subtotal.quantize(Decimal("0.01")), amount

    assert review_path.exists(), f"missing {review_path}"
    review = review_path.read_text(encoding="utf-8")
    assert "FLAG SUMMARY" in review
    assert "HOW TO REVIEW" in review
    for line in package.lines:
        assert f"## Line {line.line_no}" in review, f"missing line {line.line_no}"

    mention = exp.get("body_must_mention")
    if mention:
        assert mention.lower() in body.lower(), (
            f"email body missing {mention!r}: {body[:400]!r}"
        )

    print(f"PASS: {filename} subtotal={package.subtotal} review={review_path}")


def _check_one(filename: str) -> None:
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            print(f"\n=== {filename} (attempt {attempt + 1}) ===")
            package = _run_one(filename)
            _assert_package(filename, package)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"FAIL attempt {attempt + 1}: {exc}")
            if attempt == 0:
                print("  retrying once (flakiness protocol)...")
    raise AssertionError(f"{filename} failed twice: {last_exc}")


def main() -> None:
    for filename in sorted(EXPECTED):
        _check_one(filename)
    print("\nall assembly checks passed")


if __name__ == "__main__":
    main()
