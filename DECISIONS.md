# Locked decisions

Working decision log for the RFQ?quote prototype. These choices are fixed for the assessment build.

## Selected vs rejected

| Decision | Selected | Rejected |
|----------|----------|----------|
| Problem | P1 RFQ?quote (logic-heavy, mocked externals) | Problem 2 |
| Orchestration | LangGraph as state machine, 7 nodes (ingest, extract, match, enrich, assemble, validate_edits, mocks), interrupt-based human gate, SqliteSaver at `runs/checkpoints.db` | ReAct/agent loop, Send fan-out, in-memory checkpointer |
| Time-box | If graph interrupt+resume not working by ~hour 2.5, fall back to calling the same 7 node functions sequentially from `process()` / `finalize()` | — |
| LLM confinement | Exactly 2 call sites (extraction, email prose), Claude, temperature 0 | LLM matching, LLM pricing, LLM-computed totals |
| PDF ingest | `pypdf` text extraction; if extracted text < 50 chars, retry once via Claude multimodal document block, then fail loud | pdfplumber, Textract, OCR |
| Email ingest | stdlib `email` on `.eml` files | live IMAP/SMTP |
| Matching | 3-rung deterministic scorer — (1) exact SKU hit is authoritative, (2) SKU present but not in catalog ? status `unknown_sku`, never substituted, nearest-SKU edit-distance hints attached for the reviewer only, (3) no SKU ? weighted attribute scorer. Score is NORMALIZED over attributes available on both the RFQ line and the catalog row (a perfect clamp match on 3 of 7 attributes scores 1.0, not 0.50). Bands on normalized score: `attribute_match` requires ? 0.80 AND margin ? 0.10 over the #2 candidate; 0.55–0.80 or margin < 0.10 ? `low_confidence` (top candidate suggested, not priced); < 0.55 ? `no_match`. `unknown_sku` and `no_match` are DISTINCT statuses (different reviewer messaging). Pressure is scored directionally: catalog working pressure must be ? requested (an under-rated hose is a fail, not a near match). Extraction confidence caps the band (a low-confidence extraction can never produce an auto-accepted match). | LLM match, embeddings, fuzzy SKU autocorrect, ±10% pressure similarity |
| Attribute weights (pre-normalization) | `hose_id_in` 0.25, construction/SAE 0.20, `category_hint` 0.15, working_pressure 0.10, material 0.10, end fittings 0.10, length 0.10 | — |
| Money | `decimal.Decimal` everywhere | float |
| Assembly | Code builds all numbers/tables; LLM writes email prose from injected numbers; numeric guard asserts every number in the email exists in the package | LLM-generated quote, fully templated email |
| Review | Reviewer edits `runs/<id>/quote_package.json`, finalize re-validates, RECOMPUTES all derived values, requires `approved:true` | web UI, interactive prompts |
| Externals | Mocked (`sent_email.txt`, `intacct_payload.json`) | live Sage Intacct, live email |

## Cut list (will NOT build)

- Web UI
- Live integrations (Sage Intacct, email send/receive)
- Formats beyond the 4 sample RFQs
- CI / Docker / tests
- Embeddings
- Send fan-out
- ReAct
- Multi-agent
- Inbox poller / classifier
