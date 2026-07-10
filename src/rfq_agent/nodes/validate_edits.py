"""Validate-edits node: re-import human JSON edits and recompute derived values."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from pydantic import ValidationError

from rfq_agent.catalog.loader import CatalogIndex, load_catalog_index
from rfq_agent.config import CATALOG_CSV, RUNS_DIR
from rfq_agent.normalize import nearest_skus, normalize_sku
from rfq_agent.nodes.assemble import (
    _draft_email_body,
    _max_lead_time,
    _render_review_md,
    build_flag_summary,
)
from rfq_agent.schemas import (
    LineOverride,
    MatchStatus,
    PackageStatus,
    QuoteLine,
    QuotePackage,
    RFQDocument,
)

TWOPLACES = Decimal("0.01")


class FinalizeBlockedError(Exception):
    """Human-readable finalize gate failure (unresolved lines / not approved)."""


class FinalizeValidationError(Exception):
    """quote_package.json failed schema validation after human edit."""


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _price_line(line: QuoteLine, sku: str, catalog: CatalogIndex) -> QuoteLine:
    product = catalog.by_sku[normalize_sku(sku)]
    unit = product.unit_price_usd
    extended = _quantize(line.extracted.quantity * unit)
    match = line.match.model_copy(
        update={
            "matched_sku": product.sku,
            "needs_human_review": False,
        }
    )
    return line.model_copy(
        update={
            "match": match,
            "catalog_description": product.description,
            "unit_price": unit,
            "uom": product.uom,
            "lead_time_days": product.lead_time_days,
            "stock_qty": product.stock_qty,
            "extended_price": extended,
            "flags": [f for f in line.flags if f not in {
                "unknown_sku",
                "low_confidence",
                "no_match",
                "stock_shortfall",
                "deadline_risk",
            }],
        }
    )


def _apply_overrides(
    package: QuotePackage, catalog: CatalogIndex
) -> tuple[list[QuoteLine], list[str], list[str]]:
    """Apply overrides; return (lines, audit_notes, removed_notes)."""
    lines_by_no = {ln.line_no: ln for ln in package.lines}
    audit: list[str] = []
    removed_notes: list[str] = []
    removed_nos: set[int] = set()

    for ov in package.overrides:
        if ov.line_no not in lines_by_no and ov.line_no not in removed_nos:
            raise FinalizeBlockedError(
                f"Override references unknown line_no {ov.line_no}."
            )
        if ov.line_no in removed_nos:
            continue
        line = lines_by_no[ov.line_no]
        action = ov.action.strip()

        if action == "remove_line":
            del lines_by_no[ov.line_no]
            removed_nos.add(ov.line_no)
            note = (
                f"removed_lines: line {ov.line_no} removed by reviewer"
                + (f" ({ov.note})" if ov.note else "")
            )
            removed_notes.append(note)
            audit.append(note)
            continue

        if action == "accept_suggested":
            sku = line.match.matched_sku
            if not sku:
                raise FinalizeBlockedError(
                    f"Line {ov.line_no}: accept_suggested but no suggested SKU "
                    "is stored on the match result."
                )
            # Status stays low_confidence (or prior band); human-approved via override.
            lines_by_no[ov.line_no] = _price_line(line, sku, catalog)
            audit.append(
                f"Line {ov.line_no}: accept_suggested -> {sku}"
                + (f" ({ov.note})" if ov.note else "")
            )
            continue

        if action == "replace_sku":
            if not ov.replacement_sku:
                raise FinalizeBlockedError(
                    f"Line {ov.line_no}: replace_sku requires replacement_sku."
                )
            key = normalize_sku(ov.replacement_sku)
            product = catalog.by_sku.get(key)
            if product is None:
                hints = nearest_skus(key, list(catalog.by_sku.keys()), n=3)
                raise FinalizeBlockedError(
                    f"Line {ov.line_no}: replacement SKU {ov.replacement_sku!r} "
                    f"is not in the catalog. Nearby SKUs: {', '.join(hints) or 'none'}."
                )
            lines_by_no[ov.line_no] = _price_line(line, product.sku, catalog)
            audit.append(
                f"Line {ov.line_no}: replace_sku -> {product.sku}"
                + (f" ({ov.note})" if ov.note else "")
            )
            continue

        raise FinalizeBlockedError(
            f"Line {ov.line_no}: unknown override action {action!r}. "
            "Use accept_suggested, replace_sku, or remove_line."
        )

    remaining = sorted(lines_by_no.values(), key=lambda ln: ln.line_no)
    return remaining, audit, removed_notes


def _recompute_commercial(
    lines: list[QuoteLine], catalog: CatalogIndex
) -> tuple[list[QuoteLine], Decimal]:
    """Re-fetch catalog commercial fields for every priced/overridden SKU."""
    out: list[QuoteLine] = []
    subtotal = Decimal("0.00")
    for line in lines:
        sku = line.match.matched_sku
        # Price when we have a SKU and the line is not still awaiting review,
        # OR when an override cleared needs_human_review via _price_line.
        if sku and not line.match.needs_human_review:
            priced = _price_line(line, sku, catalog)
            # stock shortfall flag
            flags = list(priced.flags)
            if priced.stock_qty is not None and line.extracted.quantity > Decimal(
                priced.stock_qty
            ):
                if "stock_shortfall" not in flags:
                    flags.append("stock_shortfall")
            priced = priced.model_copy(update={"flags": flags})
            assert priced.extended_price is not None
            subtotal += priced.extended_price
            out.append(priced)
        elif sku and line.match.status in {
            MatchStatus.exact_sku,
            MatchStatus.attribute_match,
        }:
            priced = _price_line(line, sku, catalog)
            assert priced.extended_price is not None
            subtotal += priced.extended_price
            out.append(priced)
        else:
            # Still unpriced
            out.append(
                line.model_copy(
                    update={
                        "unit_price": None,
                        "extended_price": None,
                        "lead_time_days": None,
                        "stock_qty": None,
                        "catalog_description": None,
                    }
                )
            )
    return out, _quantize(subtotal)


def _unresolved_lines(lines: list[QuoteLine], overrides: list[LineOverride]) -> list[int]:
    covered = {ov.line_no for ov in overrides}
    return [
        ln.line_no
        for ln in lines
        if ln.match.needs_human_review and ln.line_no not in covered
    ]


def validate_edits(
    run_id: str,
    *,
    reject: bool = False,
    reject_reason: str | None = None,
    document: RFQDocument | None = None,
) -> QuotePackage:
    """Re-read package from disk, apply overrides, recompute, enforce gate."""
    path = RUNS_DIR / run_id / "quote_package.json"
    if not path.exists():
        raise FinalizeBlockedError(
            f"No quote package found for run {run_id}. "
            f"Expected file at {path}."
        )

    try:
        package = QuotePackage.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise FinalizeValidationError(
            "quote_package.json failed validation after your edits:\n"
            f"{exc}\n"
            "Fix the JSON and run finalize again."
        ) from exc

    if reject:
        notes = reject_reason or "Rejected by reviewer."
        package = package.model_copy(
            update={
                "status": PackageStatus.rejected,
                "approved": False,
                "reviewer_notes": notes,
            }
        )
        path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return package

    catalog = load_catalog_index(CATALOG_CSV)

    # Gate before applying: unresolved needs_human_review must have overrides.
    unresolved = _unresolved_lines(package.lines, package.overrides)
    if unresolved:
        listed = ", ".join(f"line {n}" for n in unresolved)
        raise FinalizeBlockedError(
            f"Cannot finalize: these lines still need a decision (add an "
            f"override): {listed}. Also set approved to true when ready."
        )
    if not package.approved:
        raise FinalizeBlockedError(
            "Cannot finalize: set approved to true in quote_package.json "
            "after resolving flagged lines."
        )

    before_subtotal = package.subtotal
    changed = bool(package.overrides)
    lines, audit, removed_notes = _apply_overrides(package, catalog)
    lines, subtotal = _recompute_commercial(lines, catalog)
    if subtotal != before_subtotal:
        changed = True

    # Rebuild flag summary from remaining lines + removed audit trail.
    temp = package.model_copy(update={"lines": lines, "subtotal": subtotal})
    flag_summary = build_flag_summary(temp.lines)
    flag_summary.extend(removed_notes)
    for note in audit:
        if note not in flag_summary:
            flag_summary.append(f"override: {note}")

    package = package.model_copy(
        update={
            "lines": lines,
            "subtotal": subtotal,
            "flag_summary": flag_summary,
            "status": PackageStatus.approved,
            "approved": True,
        }
    )

    # Regenerate email when overrides or recomputed totals changed the quote.
    if changed:
        if document is None:
            doc_path = RUNS_DIR / run_id / "document.json"
            if doc_path.exists():
                document = RFQDocument.model_validate_json(
                    doc_path.read_text(encoding="utf-8")
                )
        if document is not None:
            lead = _max_lead_time(package.lines)
            body, used_fallback = _draft_email_body(package, document, lead)
            new_flags = list(package.flag_summary)
            if used_fallback and "email fell back to template" not in new_flags:
                new_flags.append("email fell back to template")
            package = package.model_copy(
                update={"email_body": body, "flag_summary": new_flags}
            )
            review = _render_review_md(package, lead)
            (RUNS_DIR / run_id / "review.md").write_text(
                review + "\n", encoding="utf-8"
            )

    path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return package
