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
from email.message import EmailMessage
from pathlib import Path

from rfq_agent.config import RUNS_DIR
from rfq_agent.schemas import QuotePackage


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _money_disp(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _qty_disp(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _max_lead(package: QuotePackage) -> int | None:
    leads = [ln.lead_time_days for ln in package.lines if ln.lead_time_days is not None]
    return max(leads) if leads else None


def render_customer_quote(package: QuotePackage) -> str:
    """Customer-facing quotation document (Markdown table) -- the email attachment."""
    lead = _max_lead(package)
    lead_line = (
        f"**Estimated lead time:** {lead} business days\n\n" if lead is not None else ""
    )
    header = (
        f"# Quotation {package.quote_id}\n\n"
        "**Southeast Hose & Fitting Co.**  \n"
        "quotes@shfco.com\n\n"
        "---\n\n"
        f"**Prepared for:** {package.customer_name or '-'}  \n"
        f"**Ship to:** {package.ship_to or '-'}  \n"
        f"**Quote date:** {package.rfq_date or '-'}  \n"
        f"**Terms:** {package.terms or '-'}  \n"
        f"{lead_line}"
        "**Quote valid for:** 30 days from quote date\n\n"
    )
    rows = [
        "| Line | Part Number | Description | Qty | UOM | Unit Price | Extended |",
        "| --- | --- | --- | ---: | --- | ---: | ---: |",
    ]
    for ln in package.lines:
        if ln.unit_price is None or not ln.match.matched_sku:
            continue
        rows.append(
            f"| {ln.line_no} | {ln.match.matched_sku} | "
            f"{ln.catalog_description or '-'} | {_qty_disp(ln.extracted.quantity)} | "
            f"{ln.uom.value if ln.uom else '-'} | {_money_disp(ln.unit_price)} | "
            f"{_money_disp(ln.extended_price)} |"
        )
    rows.append(
        f"| | | | | | **Subtotal** | **{_money_disp(package.subtotal)}** |"
    )
    unavailable = [ln for ln in package.lines if "not_available" in ln.flags]
    note = ""
    if unavailable:
        items = "\n".join(
            f"- Line {ln.line_no}: {ln.extracted.sku or ln.source_text}"
            for ln in unavailable
        )
        note = (
            f"\n\n**Items not currently available** "
            f"({len(unavailable)} item(s), not included in the total):\n{items}\n"
        )
    footer = (
        "\n\n_All prices in USD. This quotation is prepared for your review; "
        "please confirm to place an order._\n"
    )
    return header + "\n".join(rows) + note + footer


def write_mocks(package: QuotePackage) -> dict[str, Path]:
    """Write mock email send + Intacct Sales Quote payload for an approved package."""
    run_dir = RUNS_DIR / package.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Markdown version renders cleanly in the review UI.
    quote_path = run_dir / "quote.md"
    quote_path.write_text(render_customer_quote(package), encoding="utf-8")

    # PDF version is the professional, portable document the email attaches.
    from rfq_agent.quote_pdf import render_quote_pdf_bytes

    pdf_bytes = render_quote_pdf_bytes(package)
    pdf_path = run_dir / "quote.pdf"
    pdf_path.write_bytes(pdf_bytes)

    attachment_name = f"Quotation-{package.quote_id}.pdf"

    # Human-readable transcript of the "sent" email, noting the attachment.
    email_path = run_dir / "sent_email.txt"
    email_path.write_text(
        f"To: {package.email_to or ''}\n"
        f"Subject: {package.email_subject or ''}\n"
        f"Attachments: {attachment_name}\n"
        f"\n"
        f"{package.email_body or ''}\n",
        encoding="utf-8",
    )

    # A real, openable email file with the quotation PDF as an actual MIME
    # attachment. This is the mock of the send; no network call is made.
    msg = EmailMessage()
    msg["From"] = "Southeast Hose & Fitting Co. <quotes@shfco.com>"
    msg["To"] = package.email_to or ""
    msg["Subject"] = package.email_subject or ""
    msg.set_content(package.email_body or "")
    msg.add_attachment(
        pdf_bytes, maintype="application", subtype="pdf", filename=attachment_name
    )
    eml_path = run_dir / "sent_email.eml"
    eml_path.write_bytes(msg.as_bytes())

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
    return {
        "quote": quote_path,
        "sent_email": email_path,
        "sent_email_eml": eml_path,
        "intacct_payload": intacct_path,
    }
