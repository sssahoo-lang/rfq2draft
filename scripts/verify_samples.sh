#!/usr/bin/env bash
# One-command verification suite for rfq2draft.
# Makes real Anthropic API calls (extraction + email). Approximate cost: pennies.
# Requires ANTHROPIC_API_KEY in the environment or a loaded .env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing venv at .venv - create it and pip install -r requirements.txt"
  exit 1
fi

run_suite() {
  local name="$1"
  local script="$2"
  echo ""
  echo "========================================"
  echo "SUITE: ${name}"
  echo "========================================"
  "$PY" "$script"
}

run_suite "schemas" scripts/verify_schemas.py
run_suite "matcher spec (offline)" scripts/verify_matcher_spec.py
run_suite "deterministic" scripts/verify_deterministic.py
run_suite "matching" scripts/verify_matching.py
run_suite "extraction (LLM)" scripts/verify_extraction.py
run_suite "assembly (LLM)" scripts/verify_assembly.py
run_suite "roundtrip (LLM)" scripts/verify_roundtrip.py

echo ""
echo "ALL SUITES GREEN"
