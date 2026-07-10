"""Assemble node: quote package + LLM #2 email prose + numeric guard."""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path

import anthropic

from rfq_agent.catalog.loader import CatalogIndex
from rfq_agent.config import ANTHROPIC_MODEL, EMAIL_GUARD_MAX_REGEN, RUNS_DIR
from rfq_agent.schemas import (
    ExtractedRFQ,
    MatchStatus,
    PackageStatus,
    QuoteLine,
    QuotePackage,
    RFQDocument,
)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "email.md"

# Dollar amounts in prose: $1,234.56 or 1234.56 USD (case-insensitive).
_DOLLAR_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d{1,2})?)|(?<![\w.])(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})\s*USD",
    re.IGNORECASE,
)


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and set "
            "ANTHROPIC_API_KEY, or export it in your shell."
        )
    return key


def _money_str(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _normalize_money_token(token: str) -> Decimal:
    return Decimal(token.replace(",", "").strip()).quantize(Decimal("0.01"))


def extract_dollar_amounts(body: str) -> list[Decimal]:
    """Return every dollar amount found in email body prose."""
    found: list[Decimal] = []
    for match in _DOLLAR_RE.finditer(body):
        raw = match.group(1) or match.group(2)
        if raw:
            found.append(_normalize_money_token(raw))
    return found


def numeric_guard_ok(body: str, subtotal: Decimal) -> tuple[bool, str]:
    """Every dollar amount in body must equal subtotal; no other amounts allowed."""
    expected = subtotal.quantize(Decimal("0.01"))
    amounts = extract_dollar_amounts(body)
    for amount in amounts:
        if amount != expected:
            return (
                False,
                f"email body contains dollar amount {amount} which is not "
                f"the subtotal {expected}",
            )
    return True, "ok"


def _is_flagged(line: QuoteLine) -> bool:
    return line.match.needs_human_review or line.unit_price is None


def _flag_sentence(line: QuoteLine) -> str:
    status = line.match.status
    sku = line.extracted.sku
    if status == MatchStatus.unknown_sku:
        hints = ", ".join(c.sku for c in line.match.candidates[:3]) or "none"
        return (
            f"Line {line.line_no}: SKU {sku} not in catalog - reviewer must "
            f"pick a replacement or remove the line. Nearest hints: {hints}."
        )
    if status == MatchStatus.low_confidence:
        return (
            f"Line {line.line_no}: low-confidence match "
            f"(suggested {line.match.matched_sku}) - needs your decision. "
            f"{line.match.rationale}"
        )
    if status == MatchStatus.no_match:
        return (
            f"Line {line.line_no}: no catalog match - needs your decision. "
            f"{line.match.rationale}"
        )
    extra = ", ".join(line.flags) if line.flags else "review required"
    return f"Line {line.line_no}: {extra}."


def build_flag_summary(quote_lines: list[QuoteLine]) -> list[str]:
    """Human-readable flag summary; first entry is the priced/attention rollup."""
    total = len(quote_lines)
    flagged = [ln for ln in quote_lines if _is_flagged(ln)]
    priced = sum(1 for ln in quote_lines if ln.unit_price is not None)
    k = len(flagged)
    if k == 0:
        summary = [f"All {total} lines priced cleanly."]
    else:
        summary = [f"{priced} of {total} lines priced; {k} need your attention."]
    for line in flagged:
        summary.append(_flag_sentence(line))
    return summary


def _max_lead_time(quote_lines: list[QuoteLine]) -> int | None:
    leads = [ln.lead_time_days for ln in quote_lines if ln.lead_time_days is not None]
    return max(leads) if leads else None


def _email_user_payload(
    package: QuotePackage,
    document: RFQDocument,
    lead_days: int | None,
) -> str:
    flagged = [ln for ln in package.lines if _is_flagged(ln)]
    flag_blocks: list[str] = []
    for line in flagged:
        cands = ", ".join(
            f"{c.sku} (score {c.score:.2f})" for c in line.match.candidates[:3]
        )
        flag_blocks.append(
            f"- Line {line.line_no}: status={line.match.status.value}; "
            f"rfq_sku={line.extracted.sku!r}; "
            f"suggested={line.match.matched_sku!r}; "
            f"rationale={line.match.rationale}; "
            f"candidates=[{cands}]"
        )
    if not flag_blocks:
        flag_blocks = ["- (none)"]

    lead = f"{lead_days} business days" if lead_days is not None else "n/a"
    their_ref = document.subject or package.quote_id
    return (
        f"Customer: {package.customer_name}\n"
        f"Contact email: {package.email_to}\n"
        f"Quote id: {package.quote_id}\n"
        f"Their reference / subject: {their_ref}\n"
        f"Line count: {len(package.lines)}\n"
        f"Subtotal (ONLY dollar amount you may write): {_money_str(package.subtotal)}\n"
        f"Lead-time summary (max over priced lines): {lead}\n"
        f"Needed by: {package.needed_by or 'not specified'}\n"
        f"Flagged items:\n" + "\n".join(flag_blocks) + "\n\n"
        "Write the email body only."
    )


def _template_email(package: QuotePackage, lead_days: int | None) -> str:
    """Deterministic fallback when LLM prose fails the numeric guard."""
    lead = f"{lead_days} business days" if lead_days is not None else "see quote"
    flags = "\n".join(f"- {s}" for s in package.flag_summary[1:]) or "- none"
    return (
        f"Hello,\n\n"
        f"Thank you for the RFQ. Please find quote {package.quote_id} for your "
        f"review. Quote subtotal: ${_money_str(package.subtotal)}. "
        f"Longest lead time among priced lines: {lead}.\n\n"
        f"Items needing your attention:\n{flags}\n\n"
        f"Line-level pricing is in the attached quote package. "
        f"Please confirm how you would like us to proceed on any flagged lines.\n\n"
        f"Best regards,\n"
        f"Sales Ops Team\n"
        f"Southeast Hose & Fitting Co.\n"
        f"quotes@shfco.com\n"
    )


def _draft_email_body(
    package: QuotePackage,
    document: RFQDocument,
    lead_days: int | None,
) -> tuple[str, bool]:
    """Return (body, used_template_fallback)."""
    client = anthropic.Anthropic(api_key=_require_api_key())
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = _email_user_payload(package, document, lead_days)
    messages: list[dict] = [{"role": "user", "content": user}]

    last_violation = ""
    for attempt in range(1 + EMAIL_GUARD_MAX_REGEN):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            temperature=0,
            system=system,
            messages=messages,
        )
        body = "".join(
            block.text for block in response.content if hasattr(block, "text")
        ).strip()
        ok, detail = numeric_guard_ok(body, package.subtotal)
        if ok:
            return body, False
        last_violation = detail
        if attempt >= EMAIL_GUARD_MAX_REGEN:
            break
        messages.append({"role": "assistant", "content": body})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Numeric guard failed: {last_violation}. "
                    f"Rewrite the body. The only allowed dollar amount is "
                    f"{_money_str(package.subtotal)}. Do not invent other "
                    f"dollar amounts. Do not restate per-line prices."
                ),
            }
        )

    return _template_email(package, lead_days), True


def _attrs_brief(line: QuoteLine) -> str:
    attrs = line.extracted.attributes
    parts = []
    for name, value in attrs.model_dump().items():
        if value is not None:
            parts.append(f"{name}={value}")
    return ", ".join(parts) if parts else "(none)"


def _render_review_md(package: QuotePackage, lead_days: int | None) -> str:
    lead = f"{lead_days} business days" if lead_days is not None else "n/a"
    lines_out: list[str] = [
        f"# Quote {package.quote_id}",
        "",
        f"- **Customer:** {package.customer_name}",
        f"- **RFQ date:** {package.rfq_date}",
        f"- **Needed by:** {package.needed_by or 'not specified'}",
        f"- **Subtotal:** ${_money_str(package.subtotal)}",
        f"- **Lead-time summary:** {lead}",
        f"- **Status:** {package.status.value}",
        "",
        "## FLAG SUMMARY",
        "",
    ]
    for item in package.flag_summary:
        lines_out.append(f"- {item}")
    lines_out.append("")

    for line in package.lines:
        lines_out.extend(
            [
                f"## Line {line.line_no}",
                "",
                "### Source (verbatim)",
                "",
                f"> {line.source_text}",
                "",
                "### Extracted",
                "",
                f"- SKU: {line.extracted.sku}",
                f"- Qty / UOM: {line.extracted.quantity} {line.extracted.uom}",
                f"- Attributes: {_attrs_brief(line)}",
                f"- Extraction confidence: {line.extracted.extraction_confidence}",
                "",
                "### Match",
                "",
                f"- Status: `{line.match.status.value}`",
                f"- Matched SKU: {line.match.matched_sku}",
                f"- Catalog description: {line.catalog_description}",
                f"- Score: {line.match.score}",
                f"- Rationale: {line.match.rationale}",
                "",
            ]
        )
        if line.unit_price is not None:
            lines_out.extend(
                [
                    "### Price",
                    "",
                    f"- Unit: ${_money_str(line.unit_price)} / {line.uom}",
                    f"- Extended: ${_money_str(line.extended_price)}",
                    f"- Lead time (days): {line.lead_time_days}",
                    f"- Stock qty: {line.stock_qty}",
                    "",
                ]
            )
        else:
            lines_out.extend(
                [
                    "### Price",
                    "",
                    "**NOT PRICED - needs your decision**",
                    "",
                    "| Candidate SKU | Score | Breakdown |",
                    "|---|---|---|",
                ]
            )
            for cand in line.match.candidates:
                breakdown = "; ".join(
                    f"{k}: {v}" for k, v in cand.breakdown.items()
                )
                lines_out.append(
                    f"| {cand.sku} | {cand.score:.2f} | {breakdown} |"
                )
            lines_out.append("")

    lines_out.extend(
        [
            "## Draft email",
            "",
            f"**To:** {package.email_to}",
            f"**Subject:** {package.email_subject}",
            "",
            "```",
            package.email_body or "",
            "```",
            "",
            "## HOW TO REVIEW",
            "",
            "You are reviewing a draft quote before anything is sent or written "
            "to the ERP.",
            "",
            f"1. Open `runs/{package.run_id}/quote_package.json` in any text editor.",
            "2. For each flagged line, add an entry under `overrides` using one of:",
            "   - `accept_suggested` -- keep the suggested SKU and price it",
            "   - `replace_sku` -- set `replacement_sku` to a catalog SKU",
            "   - `remove_line` -- drop the line from the quote",
            "3. Set `approved` to `true` when ready (or leave false / use reject).",
            "4. Run finalize (arrives in a later step):",
            f"   `python -m rfq_agent finalize {package.run_id}`",
            "",
        ]
    )

    flagged = [ln for ln in package.lines if _is_flagged(ln)]
    if flagged:
        sample = flagged[0]
        suggested = sample.match.matched_sku or (
            sample.match.candidates[0].sku if sample.match.candidates else "SHF-..."
        )
        lines_out.extend(
            [
                "Example override for this quote's flagged line:",
                "",
                "```json",
                "{",
                f'  "line_no": {sample.line_no},',
                '  "action": "replace_sku",',
                f'  "replacement_sku": "{suggested}",',
                '  "note": "Confirmed with buyer"',
                "}",
                "```",
                "",
            ]
        )
    else:
        lines_out.extend(
            [
                "No flagged lines on this quote -- you can set `approved: true` "
                "after a quick scan.",
                "",
            ]
        )

    return "\n".join(lines_out)


def assemble(
    document: RFQDocument,
    extracted: ExtractedRFQ,
    quote_lines: list[QuoteLine],
    subtotal: Decimal,
    index: CatalogIndex,
) -> QuotePackage:
    """Build QuotePackage + review.md; draft email via LLM call site #2."""
    del index  # reserved for future catalog lookups in review copy
    run_id = document.run_id
    quote_id = f"Q-{run_id}"
    lead_days = _max_lead_time(quote_lines)
    flag_summary = build_flag_summary(quote_lines)

    email_to = extracted.contact_email or document.sender
    their_ref = document.subject or (extracted.rfq_date or run_id)
    email_subject = f"Quote {quote_id} - re: {their_ref}"

    package = QuotePackage(
        run_id=run_id,
        quote_id=quote_id,
        status=PackageStatus.pending_review,
        customer_name=extracted.customer_name,
        contact_email=extracted.contact_email,
        rfq_date=extracted.rfq_date,
        needed_by=extracted.needed_by,
        ship_to=extracted.ship_to,
        terms=extracted.terms,
        lines=quote_lines,
        subtotal=subtotal,
        flag_summary=flag_summary,
        email_to=email_to,
        email_subject=email_subject,
        email_body=None,
        approved=False,
        overrides=[],
    )

    body, used_fallback = _draft_email_body(package, document, lead_days)
    if used_fallback:
        flag_summary = list(flag_summary) + ["email fell back to template"]
    package = package.model_copy(
        update={"email_body": body, "flag_summary": flag_summary}
    )

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "quote_package.json").write_text(
        package.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "review.md").write_text(
        _render_review_md(package, lead_days) + "\n",
        encoding="utf-8",
    )
    return package
