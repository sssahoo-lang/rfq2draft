"""Mocks node: write sent_email.txt and intacct_payload.json.

intacct_payload.json shape (Sales Quote create request for production):
  {
    "object": "Sales Quote",
    "idempotency_key": "<quote_id>",
    "header": {
      "customer_name", "ship_to", "terms", "rfq_reference", "quote_date"
    },
    "lines": [
      {"item", "description", "qty", "uom", "unit_price", "extended"}
    ],
    "custom_fields": {"rfq_run_id", "quote_id"}
  }
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from rfq_agent.config import RUNS_DIR
from rfq_agent.schemas import QuotePackage


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def write_mocks(package: QuotePackage) -> dict[str, Path]:
    """Write mock email send + Intacct Sales Quote payload for an approved package."""
    run_dir = RUNS_DIR / package.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    email_path = run_dir / "sent_email.txt"
    email_path.write_text(
        f"To: {package.email_to or ''}\n"
        f"Subject: {package.email_subject or ''}\n"
        f"\n"
        f"{package.email_body or ''}\n",
        encoding="utf-8",
    )

    payload = {
        "object": "Sales Quote",
        "idempotency_key": package.quote_id,
        "header": {
            "customer_name": package.customer_name,
            "ship_to": package.ship_to,
            "terms": package.terms,
            "rfq_reference": package.rfq_date,
            "quote_date": package.rfq_date,
        },
        "lines": [
            {
                "item": line.match.matched_sku,
                "description": line.catalog_description,
                "qty": format(line.extracted.quantity, "f"),
                "uom": line.uom.value if line.uom else None,
                "unit_price": _money(line.unit_price),
                "extended": _money(line.extended_price),
            }
            for line in package.lines
            if line.unit_price is not None and line.match.matched_sku
        ],
        "custom_fields": {
            "rfq_run_id": package.run_id,
            "quote_id": package.quote_id,
        },
    }
    intacct_path = run_dir / "intacct_payload.json"
    intacct_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"sent_email": email_path, "intacct_payload": intacct_path}
