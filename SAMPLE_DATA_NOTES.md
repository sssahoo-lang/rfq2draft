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

## Notes

- Nothing here is exhaustive. You do not need to handle every edge case in these files. Make a judgment call about what to solve and what to flag for human review, and defend it in your decision doc.
- You do not need to build a live Sage Intacct integration. See the problem statement for the architecture deliverable expected.
- If you want to add your own test cases on top of these, you are welcome to. These are the minimum.
