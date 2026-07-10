"""Render a professional customer-facing quotation as a real PDF.

Produces the same priced data as the Markdown quote, but as a PDF so it renders
identically and professionally in any viewer (Preview, Gmail, Outlook). This is
the document attached to the outbound email.
"""

from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from rfq_agent.schemas import QuotePackage

NAVY = colors.HexColor("#1f3a5f")
LIGHT = colors.HexColor("#eef2f7")


def _money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _qty(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _max_lead(package: QuotePackage) -> int | None:
    leads = [ln.lead_time_days for ln in package.lines if ln.lead_time_days is not None]
    return max(leads) if leads else None


def render_quote_pdf_bytes(package: QuotePackage) -> bytes:
    """Return the quotation as PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Quotation {package.quote_id}",
    )
    styles = getSampleStyleSheet()
    h_company = ParagraphStyle(
        "company", parent=styles["Title"], fontSize=18, textColor=NAVY,
        spaceAfter=2, alignment=0,
    )
    h_sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9,
                           textColor=colors.grey, spaceAfter=12)
    h_title = ParagraphStyle("qtitle", parent=styles["Heading2"], fontSize=13,
                             textColor=colors.black, spaceBefore=6, spaceAfter=8)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9.5, leading=14)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=11)
    cell_r = ParagraphStyle("cellr", parent=cell, alignment=2)
    head = ParagraphStyle("head", parent=styles["Normal"], fontSize=8.5,
                          textColor=colors.white, fontName="Helvetica-Bold")
    head_r = ParagraphStyle("headr", parent=head, alignment=2)
    footer = ParagraphStyle("footer", parent=styles["Normal"], fontSize=8,
                            textColor=colors.grey, spaceBefore=16)

    story = [
        Paragraph("Southeast Hose &amp; Fitting Co.", h_company),
        Paragraph("quotes@shfco.com", h_sub),
        Paragraph(f"Quotation {package.quote_id}", h_title),
    ]

    lead = _max_lead(package)
    meta_bits = [
        f"<b>Prepared for:</b> {package.customer_name or '-'}",
        f"<b>Ship to:</b> {package.ship_to or '-'}",
        f"<b>Quote date:</b> {package.rfq_date or '-'}",
        f"<b>Terms:</b> {package.terms or '-'}",
    ]
    if lead is not None:
        meta_bits.append(f"<b>Estimated lead time:</b> {lead} business days")
    meta_bits.append("<b>Quote valid for:</b> 30 days from quote date")
    story.append(Paragraph("<br/>".join(meta_bits), meta))
    story.append(Spacer(1, 14))

    header = [
        Paragraph("Line", head), Paragraph("Part Number", head),
        Paragraph("Description", head), Paragraph("Qty", head_r),
        Paragraph("UOM", head), Paragraph("Unit Price", head_r),
        Paragraph("Extended", head_r),
    ]
    rows = [header]
    for ln in package.lines:
        if ln.unit_price is None or not ln.match.matched_sku:
            continue
        rows.append([
            Paragraph(str(ln.line_no), cell),
            Paragraph(ln.match.matched_sku, cell),
            Paragraph(ln.catalog_description or "-", cell),
            Paragraph(_qty(ln.extracted.quantity), cell_r),
            Paragraph(ln.uom.value if ln.uom else "-", cell),
            Paragraph(_money(ln.unit_price), cell_r),
            Paragraph(_money(ln.extended_price), cell_r),
        ])
    subtotal_row = [
        Paragraph("", cell), Paragraph("", cell), Paragraph("", cell),
        Paragraph("", cell), Paragraph("", cell),
        Paragraph("<b>Subtotal</b>", cell_r),
        Paragraph(f"<b>{_money(package.subtotal)}</b>", cell_r),
    ]
    rows.append(subtotal_row)

    col_widths = [0.45, 1.35, 2.35, 0.5, 0.5, 0.85, 0.95]
    table = Table(rows, colWidths=[w * inch for w in col_widths], repeatRows=1)
    last = len(rows) - 1
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, last - 1), [colors.white, LIGHT]),
        ("LINEBELOW", (0, 0), (-1, last - 1), 0.4, colors.HexColor("#c9d3e0")),
        ("LINEABOVE", (0, last), (-1, last), 0.8, NAVY),
        ("TOPPADDING", (0, last), (-1, last), 8),
    ]))
    story.append(table)
    story.append(Paragraph(
        "All prices in USD. This quotation is prepared for your review; "
        "please confirm to place an order.", footer))

    doc.build(story)
    return buf.getvalue()
