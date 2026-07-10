"""Attribute scorer helpers for match rung 3 (deterministic, no LLM)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rfq_agent.catalog.loader import CatalogIndex
from rfq_agent.config import (
    CANDIDATES_KEPT,
    EXTRACTION_CONFIDENCE_FLOOR,
    MARGIN_REQUIRED,
    MATCH_WEIGHTS,
    THRESHOLD_ATTRIBUTE_MATCH,
    THRESHOLD_LOW_CONFIDENCE,
)
from rfq_agent.normalize import (
    build_end_lookup,
    canon_category,
    canon_construction,
    canon_end,
    canon_material,
    canon_size,
    length_to_feet,
)
from rfq_agent.schemas import (
    ExtractedLine,
    LineAttributes,
    MatchCandidate,
    MatchResult,
    MatchStatus,
    Product,
)


@dataclass(frozen=True)
class CanonicalLine:
    """Canonicalized RFQ line attributes used by the scorer."""

    hose_id_in: str | None
    construction: str | None
    working_pressure_psi: int | None
    material: str | None
    length_ft: Decimal | None
    ends: tuple[str, ...]
    category: str | None


def _collect_end_values(index: CatalogIndex) -> set[str]:
    values: set[str] = set()
    for product in index.products:
        if product.end_a:
            values.add(product.end_a)
        if product.end_b:
            values.add(product.end_b)
    return values


def canonicalize_attributes(
    attrs: LineAttributes, end_lookup: dict[str, str]
) -> CanonicalLine:
    """Canonicalize LineAttributes for scoring."""
    ends: list[str] = []
    for raw_end in (attrs.end_a, attrs.end_b):
        canon = canon_end(raw_end, end_lookup)
        if canon is not None:
            ends.append(canon)
    length = attrs.length_ft
    if length is not None:
        length = length_to_feet(length)
    return CanonicalLine(
        hose_id_in=canon_size(attrs.hose_id_in),
        construction=canon_construction(attrs.construction),
        working_pressure_psi=attrs.working_pressure_psi,
        material=canon_material(attrs.material),
        length_ft=length,
        ends=tuple(ends),
        category=canon_category(attrs.category_hint),
    )


def _available_weight_keys(canon: CanonicalLine) -> list[str]:
    keys: list[str] = []
    if canon.hose_id_in is not None:
        keys.append("hose_id_in")
    if canon.construction is not None:
        keys.append("construction")
    if canon.category is not None:
        keys.append("category_hint")
    if canon.working_pressure_psi is not None:
        keys.append("working_pressure")
    if canon.material is not None:
        keys.append("material")
    if canon.ends:
        keys.append("end_fittings")
    if canon.length_ft is not None:
        keys.append("length")
    return keys


def _category_gate_ok(line_cat: str, product_cat: str) -> bool:
    if line_cat == "ANY_HOSE":
        return "Hose" in product_cat
    return line_cat == product_cat


def _product_ends(product: Product, end_lookup: dict[str, str]) -> list[str]:
    ends: list[str] = []
    for raw in (product.end_a, product.end_b):
        if raw is None:
            continue
        # Catalog ends are already canonical; keep as-is (lookup is identity for them).
        ends.append(raw)
    return ends


def _score_product(
    canon: CanonicalLine,
    product: Product,
    available: list[str],
    denominator: float,
    end_lookup: dict[str, str],
) -> tuple[float, dict[str, str]] | None:
    """Return (score, breakdown) or None if gated out."""
    # Gates
    if canon.category is not None:
        if not _category_gate_ok(canon.category, product.category):
            return None
    if canon.hose_id_in is not None:
        if product.hose_id_in is None or product.hose_id_in != canon.hose_id_in:
            return None
    if canon.working_pressure_psi is not None and product.working_pressure_psi is not None:
        if product.working_pressure_psi < canon.working_pressure_psi:
            return None

    earned = 0.0
    breakdown: dict[str, str] = {}

    for key in available:
        weight = MATCH_WEIGHTS[key]
        if key == "hose_id_in":
            earned += weight
            breakdown[key] = f"match: {canon.hose_id_in}"
        elif key == "category_hint":
            earned += weight
            if canon.category == "ANY_HOSE":
                breakdown[key] = f"match: ANY_HOSE -> {product.category}"
            else:
                breakdown[key] = f"match: {product.category}"
        elif key == "construction":
            if product.construction == canon.construction:
                earned += weight
                breakdown[key] = f"match: {canon.construction}"
            else:
                breakdown[key] = (
                    f"mismatch: {product.construction} vs {canon.construction}"
                    if product.construction
                    else "missing on product"
                )
        elif key == "material":
            if product.material == canon.material:
                earned += weight
                breakdown[key] = f"match: {canon.material}"
            else:
                breakdown[key] = (
                    f"mismatch: {product.material} vs {canon.material}"
                    if product.material
                    else "missing on product"
                )
        elif key == "working_pressure":
            if product.working_pressure_psi is None:
                breakdown[key] = "missing on product"
            else:
                # Survived gate => product psi >= requested
                earned += weight
                breakdown[key] = (
                    f"pass: {product.working_pressure_psi} >= "
                    f"{canon.working_pressure_psi}"
                )
        elif key == "end_fittings":
            prod_ends = sorted(_product_ends(product, end_lookup))
            line_ends = sorted(canon.ends)
            if prod_ends == line_ends:
                earned += weight
                breakdown[key] = f"match: {', '.join(line_ends)}"
            else:
                # Partial credit: exactly one of two ends matches
                if len(line_ends) == 2:
                    matches = sum(1 for e in line_ends if e in prod_ends)
                    if matches == 1:
                        earned += weight * 0.5
                        breakdown[key] = (
                            f"partial: line [{', '.join(line_ends)}] vs "
                            f"product [{', '.join(prod_ends) or 'none'}]"
                        )
                    else:
                        breakdown[key] = (
                            f"mismatch: line [{', '.join(line_ends)}] vs "
                            f"product [{', '.join(prod_ends) or 'none'}]"
                        )
                elif len(line_ends) == 1:
                    if line_ends[0] in prod_ends:
                        earned += weight
                        breakdown[key] = f"match: {line_ends[0]}"
                    else:
                        breakdown[key] = (
                            f"mismatch: line [{line_ends[0]}] vs "
                            f"product [{', '.join(prod_ends) or 'none'}]"
                        )
                else:
                    breakdown[key] = "mismatch"
        elif key == "length":
            if product.length_ft is None:
                breakdown[key] = "missing on product"
            else:
                prod_len = length_to_feet(product.length_ft)
                if prod_len == canon.length_ft:
                    earned += weight
                    breakdown[key] = f"match: {canon.length_ft} ft"
                else:
                    breakdown[key] = (
                        f"mismatch: {prod_len} ft vs {canon.length_ft} ft"
                    )

    score = earned / denominator if denominator > 0 else 0.0
    return score, breakdown


def _matched_attr_phrases(breakdown: dict[str, str]) -> list[str]:
    phrases: list[str] = []
    labels = {
        "hose_id_in": "hose ID",
        "construction": "construction",
        "category_hint": "category",
        "working_pressure": "working pressure",
        "material": "material",
        "end_fittings": "end fittings",
        "length": "length",
    }
    for key, label in labels.items():
        value = breakdown.get(key, "")
        if value.startswith("match:") or value.startswith("pass:"):
            detail = value.split(":", 1)[1].strip()
            phrases.append(f"{label} {detail}")
    return phrases


def _build_rationale(
    status: MatchStatus,
    top: MatchCandidate | None,
    second: MatchCandidate | None,
    available_count: int,
    survivors: list[MatchCandidate],
    confidence_capped: bool,
) -> str:
    if status == MatchStatus.no_match:
        return (
            "No catalog product survived attribute gates / score thresholds "
            "for the specified attributes."
        )

    assert top is not None
    if status == MatchStatus.low_confidence and len(survivors) >= 2:
        top_score = survivors[0].score
        tied = [c for c in survivors if abs(c.score - top_score) < 1e-9]
        if len(tied) >= 2 and top_score >= THRESHOLD_ATTRIBUTE_MATCH:
            base = (
                f"{len(tied)} products fit the specified attributes equally; "
                "human selection required."
            )
            if confidence_capped:
                base += (
                    f" Extraction confidence below "
                    f"{EXTRACTION_CONFIDENCE_FLOOR:.2f} also caps auto-accept."
                )
            return base

    phrases = _matched_attr_phrases(top.breakdown)
    matched_bit = ", ".join(phrases) if phrases else "specified attributes"
    parts = [
        f"Matched on {matched_bit}. "
        f"Score {top.score:.2f} ({available_count} of {available_count} "
        f"specified attributes credited on top candidate {top.sku})."
    ]
    if second is not None:
        mismatch_notes = [
            v for v in second.breakdown.values() if v.startswith("mismatch")
        ]
        why = mismatch_notes[0] if mismatch_notes else "lower attribute credit"
        parts.append(
            f"Runner-up {second.sku} scored {second.score:.2f} ({why})."
        )
    if confidence_capped:
        parts.append(
            f"Extraction confidence below {EXTRACTION_CONFIDENCE_FLOOR:.2f} "
            "capped the band at low_confidence."
        )
    return " ".join(parts)


def score_attribute_line(
    line: ExtractedLine, index: CatalogIndex
) -> MatchResult:
    """Rung 3: weighted attribute scorer with gates, margin, and confidence cap."""
    end_lookup = build_end_lookup(_collect_end_values(index))
    canon = canonicalize_attributes(line.attributes, end_lookup)
    available = _available_weight_keys(canon)
    denominator = sum(MATCH_WEIGHTS[k] for k in available)

    if denominator <= 0:
        return MatchResult(
            status=MatchStatus.no_match,
            matched_sku=None,
            score=None,
            rationale=(
                "No scorable attributes present on the RFQ line after "
                "canonicalization."
            ),
            candidates=[],
            needs_human_review=True,
        )

    scored: list[MatchCandidate] = []
    for product in index.products:
        result = _score_product(canon, product, available, denominator, end_lookup)
        if result is None:
            continue
        score, breakdown = result
        scored.append(
            MatchCandidate(
                sku=product.sku,
                score=score,
                breakdown=breakdown,
                note=None,
            )
        )

    scored.sort(key=lambda c: (-c.score, c.sku))
    survivors = scored
    if not survivors:
        top_n: list[MatchCandidate] = []
    else:
        # Never hide a same-score tie from the reviewer (e.g. RFQ-003 line 6).
        top_score = survivors[0].score
        tied_at_top = [c for c in survivors if abs(c.score - top_score) < 1e-9]
        if len(tied_at_top) > CANDIDATES_KEPT:
            top_n = tied_at_top
        else:
            top_n = survivors[:CANDIDATES_KEPT]

    if not survivors or survivors[0].score < THRESHOLD_LOW_CONFIDENCE:
        return MatchResult(
            status=MatchStatus.no_match,
            matched_sku=None,
            score=survivors[0].score if survivors else None,
            rationale=_build_rationale(
                MatchStatus.no_match, None, None, len(available), survivors, False
            ),
            candidates=top_n,
            needs_human_review=True,
        )

    top = survivors[0]
    second = survivors[1] if len(survivors) > 1 else None
    margin_ok = second is None or (top.score - second.score) >= MARGIN_REQUIRED
    high_enough = top.score >= THRESHOLD_ATTRIBUTE_MATCH
    confidence_capped = line.extraction_confidence < EXTRACTION_CONFIDENCE_FLOOR

    if high_enough and margin_ok and not confidence_capped:
        status = MatchStatus.attribute_match
        matched_sku = top.sku
        needs_review = False
    else:
        status = MatchStatus.low_confidence
        matched_sku = top.sku  # suggestion only - never priced by enrich
        needs_review = True

    return MatchResult(
        status=status,
        matched_sku=matched_sku,
        score=top.score,
        rationale=_build_rationale(
            status, top, second, len(available), survivors, confidence_capped
        ),
        candidates=top_n,
        needs_human_review=needs_review,
    )
