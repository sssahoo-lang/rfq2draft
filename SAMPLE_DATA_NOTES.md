# Problem 1 - Sample Data

This package contains sample data for the RFQ-to-Quote drafting problem. Everything here is synthetic. Company names, contacts, and addresses are fictional.

## Contents

### `/catalog/product_catalog.csv`
The product catalog your agent will match RFQ line items against. 33 SKUs spanning hydraulic hose, pneumatic hose, PTFE hose, pre-made hose assemblies, fittings (JIC, NPT, ORFS), and clamps. Fields include SKU, description, category, hose ID, construction standard, working pressure, material, length, end fittings, unit price, UOM, lead time, and stock quantity.

Treat this file as a stand-in for a product master that would live in the client's ERP in production.

### `/rfqs/` — 4 sample RFQs in varying formats

1. **RFQ-001_CarolinaFluidPower.pdf** — Clean PDF with a structured line item table. All line items have valid SKUs.

2. **RFQ-002_GulfCoastIndustrial.eml** — Email body, numbered list format. Mix of SKUs and attribute-only descriptions.

3. **RFQ-003_PiedmontHydraulics.pdf** — Informal PDF. No SKUs at all. Every line must be matched on attributes (hose ID, construction, pressure rating, end fittings, material). Includes one line with limited information that should likely be flagged as low-confidence.

4. **RFQ-004_DeltaPower.eml** — Plain-text email with a monospaced table. All SKUs provided, but at least one does not exist in the catalog and should be flagged.

### Added demo RFQs (not part of the original package)

These are self-authored to show the agent generalizes beyond the four provided samples.

5. **RFQ-005_SummitFluidSystems.eml** — Rush email mixing exact SKUs, attribute-only lines, an underspecified "recommend something" line (flagged low-confidence), and a customer-supplied SKU that is not in the catalog (flagged `unknown_sku`). It also deliberately triggers two advisory flags that none of the four provided RFQs reach: a **stock shortfall** (500 ft ordered against 350 in stock) and a **deadline risk** (a 5-day requested window against 7–10 day lead times). One document that exercises every match status plus both availability flags. Expected subtotal $17,037.50; a deterministic regression check for it lives in `scripts/verify_deterministic.py` (check E).

## Notes

- Nothing here is exhaustive. You do not need to handle every edge case in these files. Make a judgment call about what to solve and what to flag for human review, and defend it in your decision doc.
- You do not need to build a live Sage Intacct integration. See the problem statement for the architecture deliverable expected.
- If you want to add your own test cases on top of these, you are welcome to. These are the minimum.
