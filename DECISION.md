# Decision Doc — Automated RFQ-to-Quote Drafting

**Problem 1 · Fastlane AI Agent Engineer Assessment**

---

## Problem and rationale

I picked Problem 1 — turning an incoming distributor RFQ into a drafted quote
and reply email — because it lines up almost exactly with work I have already
done. In my robotic process automation internship I built flows that pulled
data out of incoming emails, cleaned it, and organized it into something the
business could act on, which is the heart of this problem. I have also worked
in inventory management, so matching line items to a product catalog and
reasoning about pricing and availability is familiar ground for me rather than
something I would be figuring out for the first time. That made it a clear
choice over the marketing-content alternative, which sits further from my
hands-on background. It also happens to be interesting for the right reason:
the hard part is *judgment*, not text generation — matching a plain-English
line like "hydraulic hose, 2-wire braid, 3/4 inch" to the one right catalog
SKU, and knowing when *not* to answer. That shaped my whole approach: the LLM
handles language, deterministic code handles anything involving money, and a
human approves before anything leaves the building. The problem also shipped
with real fixture data (four RFQs, a 33-SKU catalog), which let me prove
correctness to the penny rather than argue it.

## Approach and build-vs-buy

I built a custom Python pipeline orchestrated with **LangGraph**, using the
LLM (Claude) at exactly two points: reading the RFQ into structured line items,
and writing the prose of the reply email. Everything between — catalog
matching, pricing, totals — is ordinary, testable code, because a system that
can transpose a digit should never be the source of a number on a quote.

I rejected a **no-code build (Make / n8n)**. Its real strength is prebuilt
connectors for inboxes and ERPs, which this assessment mocks anyway, while the
core — a weighted matching engine with confidence thresholds and a rationale
per line — would degenerate into custom JavaScript inside workflow nodes: the
same logic with worse version control and testing. In production I'd still use
no-code where it belongs, as the inbox *trigger* in front of this pipeline. I
also rejected **letting the LLM do the matching**: its confidence is
uncalibrated, its "reasoning" is a story told after the fact rather than the
actual decision procedure, and its failure mode is a confidently wrong part
number — the one error this client cannot afford ("low-confidence matches must
be flagged, not silently guessed").

## Architecture

A staged pipeline with a hard human gate in the middle:

1. **Ingest** the PDF or email into normalized text (the inbox is mocked).
2. **Extract** (LLM call 1): line items, quantities, and specs, each with a
   confidence score and the *verbatim source text* kept for review.
3. **Match** — a three-rung deterministic matcher: an exact SKU is
   authoritative; a SKU not in the catalog is flagged, never substituted; a
   line with no SKU goes to a weighted attribute scorer with hard gates (wrong
   diameter or under-rated pressure disqualifies — a hose below spec is a
   safety miss, not a near-match) and a score normalized over what the customer
   actually specified.
4. **Price** — unit price, lead time, and totals from the catalog in exact
   decimal math. No LLM output is ever the source of a number.
5. **Assemble** the reviewable package: each line shows what the customer wrote,
   what the agent matched and *why*, and the price — plus a customer-facing PDF
   quote and a reply email (LLM call 2) whose only permitted number is the
   subtotal, verified by code.
6. **Review** — the pipeline pauses on a checkpointed LangGraph interrupt; the
   coordinator reviews in a Streamlit screen or by editing the same JSON file,
   and it won't proceed while any flag is unresolved.
7. **Finalize** — on approval it recomputes every derived number from the edited
   fields, then emits the reply email with the PDF quote attached and a Sage
   Intacct record — both mocked (a real, openable `.eml` and a production-shaped
   JSON payload).

The review interface is a thin window over the same functions the CLI uses; the
system runs end to end from the terminal without it. I built it only after the
core loop was verified — a legible screen communicates the agent's reasoning to
a non-technical coordinator far better than raw JSON, and it adds no pipeline
logic.

## Failure modes and human-in-the-loop

- **LLM returns garbage:** a strict schema rejects malformed extraction,
  retries once with the errors attached, then fails loudly rather than
  forwarding nonsense.
- **LLM returns plausible-but-thin output:** the deterministic side catches it.
  When extraction once dropped the word "pneumatic" from a line during the
  build, the result was not a wrong match but a line flagged "needs review" and
  excluded from the subtotal. Low confidence and scored ties are first-class
  outcomes that reach the human as questions, never guesses.
- **Reviewer edits break the math:** finalize recomputes all totals from the
  edited fields, so an edited quantity can never leave a stale price.
- **External system down (production):** the quote holds in "approved, not
  posted," the customer email is held with it, and the write retries — a
  quoted-but-unrecorded order is worse than a slow reply.

The human step is real, not ceremonial: the reviewer sees a rationale per line,
edits any field, and must resolve every flag before approval unlocks. On a
flagged line they can accept the suggestion, swap in a different SKU, keep the
line but mark it *not currently available*, or remove it — the system never
decides fulfillment for them. A single unavailable item never rejects the whole
quote; the rest is priced and sent, and the reviewer can still reject outright.

## Sage Intacct integration (production design; mocked here)

The prototype writes the payload to a file. In production:

1. The reviewer approves — **the human gate sits before any external write.**
2. A worker authenticates to the Intacct Web Services API using a dedicated
   sender ID and service user; credentials live in a secrets manager, never in
   config.
3. It upserts the distributor as a **Customer** if needed, then creates an
   **Order Entry Sales Quote** transaction — one line per approved item —
   carrying the RFQ run ID in a custom field for traceability.
4. Writes are **idempotent** (document reference keyed on the quote ID), so a
   retry after a timeout cannot create a duplicate quote.
5. Failures retry with backoff; after N attempts the run is marked "approved,
   not posted" and surfaced to the coordinator, with the outbound email held
   until the ERP write confirms.

Outbound email is designed the same way (Gmail API, OAuth service credentials,
same gate). The prototype mocks it by default but includes an **opt-in, gated**
live Gmail send — off unless the reviewer supplies credentials — to prove the
send path without depending on it.

## What would generalize

The reusable template for the next ten clients is the skeleton: document-in →
schema-validated LLM extraction → deterministic confidence-banded matcher →
priced package with per-line rationale → checkpointed human gate →
recompute-on-edit finalize → mocked delivery. What stays client-specific is
deliberately thin: the catalog schema and attribute weights, the domain
synonym table ("2-wire" means SAE 100R2AT), the extraction prompt's vocabulary,
and the ERP field mapping. To check that this holds beyond the provided data, I
wrote one additional RFQ of my own in a new format the agent had never seen; it
handled the full range end to end, including stock-shortfall and deadline-risk
warnings that none of the four supplied samples even trigger.

## What I'd do with another week

Wire the live inbox trigger and RFQ classifier; build the real Intacct
integration behind the existing gate; add a feedback loop that tunes match
thresholds from reviewer corrections; and support scanned RFQs via the
multimodal path (stubbed but untested today). At 10x catalog scale the matcher's
linear scan becomes the bottleneck — the fix is a category/hose-ID pre-filter
that narrows candidates before the *same* deterministic scorer runs, leaving the
auditable logic unchanged. I'd also grow the matcher's offline spec-test into a
regression suite seeded from historical RFQs and their approved quotes.
