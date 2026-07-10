"""Attribute and synonym normalization for catalog matching."""

from __future__ import annotations

import difflib


def normalize_sku(raw: str) -> str:
    """Uppercase and strip whitespace; keep dashes (they are part of real SKUs)."""
    return raw.strip().upper()


def nearest_skus(bad_sku: str, all_skus: list[str], n: int = 3) -> list[str]:
    """Return up to n catalog SKUs closest to bad_sku via string similarity."""
    return difflib.get_close_matches(normalize_sku(bad_sku), all_skus, n=n, cutoff=0.0)


# Attribute synonym tables (2-wire -> SAE 100R2AT etc.) are added with rung 3
# in a later step.
