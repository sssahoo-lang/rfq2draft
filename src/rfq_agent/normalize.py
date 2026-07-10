"""Attribute and synonym normalization for catalog matching."""

from __future__ import annotations

import difflib
import re
from decimal import Decimal


def normalize_sku(raw: str) -> str:
    """Uppercase and strip whitespace; keep dashes (they are part of real SKUs)."""
    return raw.strip().upper()


def nearest_skus(bad_sku: str, all_skus: list[str], n: int = 3) -> list[str]:
    """Return up to n catalog SKUs closest to bad_sku via string similarity."""
    return difflib.get_close_matches(
        normalize_sku(bad_sku), all_skus, n=n, cutoff=0.0
    )


_DECIMAL_TO_FRAC = {
    Decimal("0.25"): "1/4",
    Decimal("0.375"): "3/8",
    Decimal("0.5"): "1/2",
    Decimal("0.75"): "3/4",
    Decimal("1"): "1",
    Decimal("1.0"): "1",
}

_FRAC_SIZES = ("1/4", "3/8", "1/2", "3/4")


def canon_size(raw: str | None) -> str | None:
    """Hose ID / size to catalog form: 1/4, 3/8, 1/2, 3/4, or 1."""
    if raw is None:
        return None
    text = raw.strip().lower()
    text = text.replace('"', "").replace("''", "")
    text = re.sub(r"\binch(?:es)?\b", "", text)
    text = re.sub(r"\bin\b", "", text)
    text = text.strip().rstrip(".")
    text = re.sub(r"\s+", "", text)
    if not text:
        return None
    for frac in _FRAC_SIZES:
        if text == frac or text.startswith(frac):
            return frac
    if text in {"1", "1.0"}:
        return "1"
    try:
        as_dec = Decimal(text)
    except Exception:
        return None
    return _DECIMAL_TO_FRAC.get(as_dec)


_R2_KEYS = (
    "2-wire",
    "2 wire",
    "sae 100r2at",
    "sae 100r2",
    "100r2",
    "r2",
)
_R1_KEYS = (
    "1-wire",
    "1 wire",
    "sae 100r1at",
    "sae 100r1",
    "100r1",
    "r1",
)


def _contains_any(text: str, keys: tuple[str, ...]) -> bool:
    return any(key in text for key in keys)


def canon_construction(raw: str | None) -> str | None:
    """Map free-text construction/SAE wording to catalog construction strings."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    hit_r2 = _contains_any(text, _R2_KEYS)
    hit_r1 = _contains_any(text, _R1_KEYS)
    if hit_r1 and hit_r2:
        return raw.strip()
    if hit_r2:
        return "SAE 100R2AT"
    if hit_r1:
        return "SAE 100R1AT"
    return raw.strip()


def canon_material(raw: str | None) -> str | None:
    """Map material wording to catalog material; check stainless before steel."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    if "ptfe" in text:
        return "PTFE/SS"
    # Stainless before steel: "stainless steel" contains "steel".
    if "stainless" in text or re.search(r"\bss\b", text):
        return "Stainless Steel"
    if "carbon steel" in text or "steel" in text:
        return "Carbon Steel"
    return raw.strip()


def build_end_lookup(end_values: set[str]) -> dict[str, str]:
    """Map jic/npt/orfs tokens to the catalog's actual end strings (no hardcoded degree)."""
    lookup: dict[str, str] = {}
    for value in end_values:
        lower = value.lower()
        if "jic" in lower:
            lookup["jic"] = value
        elif "npt" in lower:
            lookup["npt"] = value
        elif "orfs" in lower:
            lookup["orfs"] = value
    return lookup


def canon_end(raw: str | None, end_lookup: dict[str, str]) -> str | None:
    """Map end-fitting wording to a catalog end string via end_lookup."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    if "orfs" in text:
        return end_lookup.get("orfs", raw.strip())
    if "jic" in text:
        return end_lookup.get("jic", raw.strip())
    if "npt" in text:
        return end_lookup.get("npt", raw.strip())
    return raw.strip()


def canon_category(raw: str | None) -> str | None:
    """Map category hints to catalog categories, or ANY_HOSE for bare 'hose'."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    if "assembly" in text:
        return "Hose Assembly"
    if "pneumatic" in text:
        return "Pneumatic Hose"
    if "hydraulic" in text:
        return "Hydraulic Hose"
    if "ptfe" in text:
        return "PTFE Hose"
    if "fitting" in text:
        return "Fitting"
    if "clamp" in text:
        return "Clamp"
    # Bare/generic "hose" with no qualifier.
    if re.search(r"\bhose\b", text):
        return "ANY_HOSE"
    return raw.strip()


def length_to_feet(value: Decimal) -> Decimal:
    """Treat values > 12 as inches and convert to feet; otherwise already feet."""
    if value > Decimal("12"):
        return value / Decimal("12")
    return value
