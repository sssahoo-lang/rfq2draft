"""Project configuration: paths, model, match thresholds, and weights."""

from __future__ import annotations

import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Single model constant for both LLM call sites (extraction + email prose).
ANTHROPIC_MODEL = "claude-sonnet-4-5"

# Paths derived from this file location (src/rfq_agent/config.py -> project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_CSV = PROJECT_ROOT / "catalog" / "product_catalog.csv"
RFQS_DIR = PROJECT_ROOT / "rfqs"
RUNS_DIR = PROJECT_ROOT / "runs"

# Pre-normalization attribute weights (see DECISIONS.md).
MATCH_WEIGHTS = {
    "hose_id_in": 0.25,  # highest: wrong ID is never an acceptable match
    "construction": 0.20,  # SAE/construction drives hydraulic vs pneumatic vs PTFE family
    "category_hint": 0.15,  # hose vs fitting vs clamp vs assembly narrows the candidate set
    "working_pressure": 0.10,  # directional: catalog PSI must be >= requested
    "material": 0.10,  # carbon vs stainless (and PTFE/SS) changes SKU and price
    "end_fittings": 0.10,  # JIC/NPT/ORFS and end pairs define assemblies and fittings
    "length": 0.10,  # assembly length (and sold-by-foot qty context) must align
}

THRESHOLD_ATTRIBUTE_MATCH = 0.80
THRESHOLD_LOW_CONFIDENCE = 0.55
MARGIN_REQUIRED = 0.10
MIN_PDF_TEXT_CHARS = 50

EXTRACTION_MAX_RETRIES = 1
EMAIL_GUARD_MAX_REGEN = 1

SKU_PATTERN = re.compile(r"SHF-[A-Z0-9-]+")
