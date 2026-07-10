"""Match node: 3-rung deterministic catalog matcher."""

from __future__ import annotations

from rfq_agent.catalog.loader import CatalogIndex
from rfq_agent.normalize import nearest_skus, normalize_sku
from rfq_agent.schemas import (
    ExtractedRFQ,
    MatchCandidate,
    MatchResult,
    MatchStatus,
)
from rfq_agent.scoring import score_attribute_line


def match_lines(extracted: ExtractedRFQ, index: CatalogIndex) -> list[MatchResult]:
    """Match each extracted line: SKU rungs 1-2, then attribute rung 3."""
    results: list[MatchResult] = []
    all_skus = list(index.by_sku.keys())

    for line in extracted.lines:
        if line.sku is None:
            results.append(score_attribute_line(line, index))
            continue

        sku_key = normalize_sku(line.sku)
        product = index.by_sku.get(sku_key)

        # Rung 1: exact SKU hit
        if product is not None:
            results.append(
                MatchResult(
                    status=MatchStatus.exact_sku,
                    matched_sku=product.sku,
                    score=None,
                    rationale=(
                        f"SKU provided on RFQ and found in catalog: "
                        f"{product.sku} - {product.description}"
                    ),
                    candidates=[],
                    needs_human_review=False,
                )
            )
            continue

        # Rung 2: SKU present but not in catalog - never substitute
        hints = nearest_skus(sku_key, all_skus, n=3)
        candidates = [
            MatchCandidate(
                sku=hint,
                score=0.0,
                breakdown={"sku": "not in catalog"},
                note=(
                    "nearest catalog SKU by string similarity - "
                    "suggestion for reviewer only"
                ),
            )
            for hint in hints
        ]
        results.append(
            MatchResult(
                status=MatchStatus.unknown_sku,
                matched_sku=None,
                score=None,
                rationale=(
                    f"SKU {line.sku} not found in catalog. Not substituted. "
                    "Nearest catalog SKUs suggested for review."
                ),
                candidates=candidates,
                needs_human_review=True,
            )
        )

    return results
