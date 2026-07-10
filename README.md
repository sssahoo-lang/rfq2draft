# rfq2draft

RFQ-to-quote drafting agent prototype for a Fastlane AI take-home assessment. Ingests distributor RFQs (PDF or email), extracts line items, matches them to a product catalog with a deterministic scorer, enriches pricing, and produces a reviewable quote package plus draft reply email for human approval before any mocked send or ERP write.

## Prerequisites

- Python 3.11+
- An Anthropic API key (`ANTHROPIC_API_KEY`)

## Setup

About 2 minutes.

```bash
cd rfq2draft
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env               # then put your key on the ANTHROPIC_API_KEY= line
```

## Quickstart (CLI)

Process the hardest sample (no SKUs; one ambiguous line the agent will not guess):

```bash
python -m rfq_agent process rfqs/RFQ-003_PiedmontHydraulics.pdf
```

Open `runs/RFQ-003_PiedmontHydraulics/review.md` -- this is what the agent did and why (extraction, match rationale, prices, draft email).

Resolve the flagged line by editing `runs/RFQ-003_PiedmontHydraulics/quote_package.json`. Set `approved` to `true` and add an override for line 6, for example:

```json
"approved": true,
"overrides": [
  {
    "line_no": 6,
    "action": "replace_sku",
    "replacement_sku": "SHF-PTFE-025",
    "note": "Buyer confirmed PTFE / chemical service"
  }
]
```

Then finalize:

```bash
python -m rfq_agent finalize RFQ-003_PiedmontHydraulics
```

That writes `runs/RFQ-003_PiedmontHydraulics/sent_email.txt` and `runs/RFQ-003_PiedmontHydraulics/intacct_payload.json`.

To reject instead:

```bash
python -m rfq_agent finalize RFQ-003_PiedmontHydraulics --reject --reason "customer pricing under negotiation"
```

## Quickstart (UI)

```bash
streamlit run app.py
```

1. Pick an RFQ in the sidebar and click **Process RFQ** (spinner while extraction + email run).
2. Review the flag summary and expanders (flagged lines open by default).
3. For a flagged line, choose a candidate and an action (accept / replace / remove).
4. Click **Approve & Finalize** (or **Reject** with a reason).

The UI writes the same `runs/<run_id>/quote_package.json` the CLI path edits; both call the same `process_run` / `finalize_run` code.

## Why each RFQ is interesting

- **RFQ-001** -- clean tabular PDF; every line has a valid SKU (exact match only).
- **RFQ-002** -- email with two SKUs plus two attribute-only lines (fuzzy / weighted match).
- **RFQ-003** -- informal PDF with no SKUs; five clear attribute matches; line 6 is deliberately sparse so the agent flags a tie instead of guessing.
- **RFQ-004** -- email table with a SKU that is not in the catalog (`SHF-H2-0625`); flagged as `unknown_sku`, never auto-substituted.

## Verify everything

```bash
./scripts/verify_samples.sh
```

Makes real Anthropic API calls (pennies). Success ends with `ALL SUITES GREEN` and covers schemas, deterministic SKU pricing, attribute matching, live extraction, assembly/email guard, and process/finalize roundtrips (happy edit, flagged override, reject).

## Costs and time

About 2 Claude calls per RFQ (extract + email). Wall time is usually tens of seconds per RFQ. API cost is typically under $0.05 per RFQ at current Sonnet pricing.

## What is mocked and why

Outbound email send and Sage Intacct writes are mocked by default: finalize writes `quote.md` (customer quotation), `sent_email.txt`, `sent_email.eml` (a real email file with the quotation attached), and a production-shaped `intacct_payload.json` under `runs/<run_id>/` instead of calling SMTP or Intacct. That keeps the assessment focused on agent logic and the human approval gate. Production auth, objects, failure handling, and where the gate sits are described in the decision doc.

### Optional: real email send via Gmail (off by default)

Sending is disabled unless you opt in. To enable it, set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` (a Gmail App Password, which requires 2-Step Verification) in `.env`. Then send an approved quote:

```bash
python -m rfq_agent send <run_id> --to you@example.com
```

Or use the "Send this quote for real via Gmail" panel in the UI. Sending only works on an approved quote, is idempotent (won't double-send without `--force`), and the sample RFQ recipient domains are fictional — send to your own address. No credentials live in the code; both values are read from the environment.

## Repo map

```
src/rfq_agent/          package: schemas, ingest, extract, match, enrich, assemble, graph, CLI
src/rfq_agent/nodes/    pipeline node functions
src/rfq_agent/prompts/  extraction.md + email.md (the only two LLM call sites)
src/rfq_agent/scoring.py  attribute scorer (rung 3)
catalog/                product_catalog.csv (ERP stand-in)
rfqs/                   four sample RFQs (PDF + .eml)
fixtures/extracted/     hand-written ExtractedRFQ JSON for offline matcher tests
runs/                   per-run artifacts + checkpoints.db (gitignored contents)
scripts/                verify_*.py and verify_samples.sh
app.py                  Streamlit review UI
DECISIONS.md            locked build decisions
```
