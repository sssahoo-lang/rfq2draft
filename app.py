"""Streamlit review UI -- thin window over runner + runs/<id>/ files.

This file contains NO pipeline logic. Every action calls the same shared
functions the CLI uses (process_run / finalize_run) and reads/writes the same
runs/<id>/quote_package.json a command-line reviewer would edit. If this UI is
removed, the system still runs end to end from the terminal.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import streamlit as st

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import CATALOG_CSV, RFQS_DIR, RUNS_DIR
from rfq_agent.runner import finalize_run, process_run
from rfq_agent.schemas import LineOverride, PackageStatus, QuotePackage

st.set_page_config(page_title="RFQ Quote Review", layout="wide")

# Human-readable labels for internal match statuses (reviewer never sees enums).
STATUS_LABEL = {
    "exact_sku": ("Matched by part number", True),
    "attribute_match": ("Matched by specifications", True),
    "low_confidence": ("Needs your review", False),
    "unknown_sku": ("Part number not in catalog", False),
    "no_match": ("No catalog match found", False),
}


def _rfq_files() -> list[Path]:
    return sorted(
        p for p in RFQS_DIR.iterdir() if p.suffix.lower() in {".pdf", ".eml"}
    )


def _load_package(run_id: str) -> QuotePackage | None:
    path = RUNS_DIR / run_id / "quote_package.json"
    if not path.exists():
        return None
    return QuotePackage.model_validate_json(path.read_text(encoding="utf-8"))


def _save_package(package: QuotePackage) -> None:
    path = RUNS_DIR / package.run_id / "quote_package.json"
    path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _catalog():
    return load_catalog_index(CATALOG_CSV)


def _is_flagged(line) -> bool:
    return line.match.needs_human_review or line.unit_price is None


def _money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _money_md(value: Decimal | None) -> str:
    # Escape the $ so Streamlit markdown does not interpret it as LaTeX math.
    return _money(value).replace("$", "\\$")


def _status_text(status: str) -> tuple[str, bool]:
    return STATUS_LABEL.get(status, (status, False))


def _confidence_text(conf: float) -> str:
    pct = int(round(conf * 100))
    if conf >= 0.85:
        return f"high confidence ({pct}%)"
    if conf >= 0.6:
        return f"medium confidence ({pct}%)"
    return f"low confidence ({pct}%)"


def _candidate_label(sku: str, catalog) -> str:
    """Human-readable option text: what the product is and what it costs."""
    product = catalog.get(sku)
    if product is None:
        return f"Use {sku}"
    price = f"{_money(product.unit_price_usd)}/{product.uom.value}"
    return f"Use {sku}  -  {product.description}  ({price})"


# ----------------------------------------------------------------------------
# Sidebar: pick a source and process it
# ----------------------------------------------------------------------------
_STATUS_LINE = {
    "approved": "Approved & finalized",
    "rejected": "Rejected",
    "pending_review": "Ready for your review",
}


def _pretty_name(run_id: str) -> str:
    tail = run_id.split("_", 1)[1] if "_" in run_id else run_id
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail)


def render_sidebar() -> None:
    rfqs = _rfq_files()
    with st.sidebar:
        st.subheader("Incoming RFQs")
        st.caption("Select one to view it. Selecting shows the quote right away.")

        options: list[str] = []
        captions: list[str] = []
        for p in rfqs:
            run = p.stem
            pkg = _load_package(run)
            rfq_id = run.split("_")[0]
            name = (pkg.customer_name if pkg and pkg.customer_name
                    else _pretty_name(run))
            if not any(c.islower() for c in name):  # tidy ALL-CAPS names
                name = name.title()
            options.append(f"{rfq_id}  ·  {name}")
            if pkg is None:
                captions.append("New  ·  not processed yet")
            else:
                fmt = "PDF" if p.suffix.lower() == ".pdf" else "Email"
                captions.append(
                    f"{fmt}  ·  {_STATUS_LINE.get(pkg.status.value, pkg.status.value)}"
                )

        choice = st.radio("Incoming RFQs", options, index=0,
                          captions=captions, label_visibility="collapsed")
        idx = options.index(choice)
        selected_rfq = rfqs[idx]
        selected_run = selected_rfq.stem
        has_package = _load_package(selected_run) is not None

        st.divider()

        if has_package:
            st.session_state.active_run = selected_run
            with st.expander("Re-run this RFQ from scratch"):
                st.caption("Discards the current draft and processes the RFQ again.")
                if st.button("Reprocess", use_container_width=True):
                    with st.spinner("Reprocessing..."):
                        result = process_run(selected_rfq)
                    st.session_state.active_run = result["run_id"]
                    st.rerun()
        else:
            st.session_state.active_run = None
            st.caption("This RFQ has not been processed yet. Processing reads it, "
                       "extracts line items, matches the catalog, and drafts a "
                       "quote and reply (about 30-60 seconds).")
            if st.button("Process this RFQ", type="primary",
                         use_container_width=True):
                with st.spinner("Reading the RFQ and drafting the quote..."):
                    result = process_run(selected_rfq)
                st.session_state.active_run = result["run_id"]
                st.rerun()


# ----------------------------------------------------------------------------
# Main review panel
# ----------------------------------------------------------------------------
_MATCH_BADGE = {
    "exact_sku": ":green-background[&nbsp;Matched by part number&nbsp;]",
    "attribute_match": ":blue-background[&nbsp;Matched by specifications&nbsp;]",
    "low_confidence": ":orange-background[&nbsp;Needs your review&nbsp;]",
    "unknown_sku": ":red-background[&nbsp;Part number not in catalog&nbsp;]",
    "no_match": ":red-background[&nbsp;No catalog match found&nbsp;]",
}


def render_line(line, done: bool, catalog, run_id: str) -> dict:
    """Render one line item; return the reviewer's decision for it."""
    label, ok = _status_text(line.match.status.value)
    is_flag = _is_flagged(line)
    header = f"Line {line.line_no}  ·  {label}"
    if line.catalog_description:
        header += f"  ({line.catalog_description})"

    decision = {"qty": line.extracted.quantity, "kind": "keep", "sku": None}
    with st.expander(header, expanded=is_flag and not done):
        st.markdown(_MATCH_BADGE.get(line.match.status.value, ""))

        # Requested  ->  Matched, side by side, so the transformation is clear.
        col_req, col_match = st.columns(2, gap="large")
        with col_req:
            st.markdown("**1 · Customer requested**")
            st.markdown(f"> {line.source_text}")
            st.caption(
                f"Read as {line.extracted.quantity} "
                f"{line.extracted.uom.value if line.extracted.uom else ''}  ·  "
                f"{_confidence_text(line.extracted.extraction_confidence)}"
            )
        with col_match:
            st.markdown("**2 · Agent selected**")
            if line.match.matched_sku:
                st.markdown(f"`{line.match.matched_sku}`")
                if line.catalog_description:
                    st.caption(line.catalog_description)
            else:
                st.markdown("_No catalog item selected_")
            st.caption(f"Why: {line.match.rationale}")

        st.markdown("**3 · Quantity & price**")
        qty = st.number_input(
            "Quantity (editable)",
            min_value=0.01,
            value=float(line.extracted.quantity),
            key=f"qty_{run_id}_{line.line_no}",
            disabled=done,
        )
        decision["qty"] = Decimal(str(qty))

        if line.unit_price is not None:
            live_total = (line.unit_price * Decimal(str(qty))).quantize(Decimal("0.01"))
            m1, m2 = st.columns(2)
            m1.metric(f"Unit price (per {line.uom.value if line.uom else 'unit'})",
                      _money(line.unit_price))
            m2.metric("Line total", _money(live_total))
        else:
            st.warning("Not priced -- this line needs your decision below.")

        # Flagged lines: ONE plain-English question.
        if is_flag and not done:
            st.markdown("---")
            st.markdown("**4 · Your decision**")
            options: list[tuple[str, str, str | None]] = []
            for c in line.match.candidates:
                options.append(("use", _candidate_label(c.sku, catalog), c.sku))
            options.append(
                ("unavailable",
                 "Keep on quote, mark as not currently available", None)
            )
            options.append(("remove", "Remove this line from the quote", None))
            if not line.match.candidates:
                st.caption("No catalog suggestions -- you can remove this line, "
                           "or edit the JSON directly for a manual SKU.")

            idx = st.radio(
                "What should we do with this line?",
                range(len(options)),
                format_func=lambda i: options[i][1],
                key=f"decide_{run_id}_{line.line_no}",
                label_visibility="collapsed",
            )
            kind, _, sku = options[idx]
            decision["kind"] = kind
            decision["sku"] = sku
        elif is_flag and done:
            st.caption("Resolved during finalize.")

    return decision


def render_review(package: QuotePackage, run_id: str) -> None:
    done = package.status.value in {"approved", "rejected"}
    flagged = [ln for ln in package.lines if _is_flagged(ln)]
    priced = [ln for ln in package.lines if ln.unit_price is not None]
    catalog = _catalog()

    # Header
    st.title("Quote review")
    top = st.columns([2, 1, 1, 1])
    top[0].markdown(f"**Customer**  \n{package.customer_name or '-'}")
    top[1].markdown(f"**Quote**  \n{package.quote_id}")
    top[2].markdown(f"**Subtotal**  \n{_money_md(package.subtotal)}")
    badge = {"approved": "APPROVED", "rejected": "REJECTED",
             "pending_review": "Awaiting your review"}
    top[3].markdown(f"**Status**  \n{badge.get(package.status.value, package.status.value)}")
    st.caption(
        f"RFQ dated {package.rfq_date or '-'}  |  needed by {package.needed_by or '-'}  "
        f"|  run: {run_id}"
    )

    # Progress banner
    if package.status.value == "approved":
        st.success("This quote is approved and finalized. Outputs are below.")
    elif package.status.value == "rejected":
        st.error(f"This quote was rejected by a reviewer. Reason: "
                 f"{package.reviewer_notes or '-'}")
        st.caption(
            "Rejection is a reviewer decision, not an automatic outcome. An "
            "unavailable line item never rejects a quote on its own. You can "
            "reopen this quote to edit and finalize it."
        )
        if st.button("Reopen this quote for editing"):
            reopened = package.model_copy(update={
                "status": PackageStatus.pending_review,
                "approved": False,
                "reviewer_notes": None,
                "overrides": [],
            })
            _save_package(reopened)
            st.rerun()
    elif flagged:
        st.warning(
            f"{len(priced)} of {len(package.lines)} lines are ready. "
            f"{len(flagged)} line(s) need your decision before you can approve."
        )
    else:
        st.success(
            f"All {len(package.lines)} lines matched cleanly. "
            "Review below and approve when ready."
        )

    st.subheader("Line items")
    decisions = {
        ln.line_no: render_line(ln, done, catalog, run_id) for ln in package.lines
    }

    if done:
        render_outputs(package, run_id)
        return

    # Approve / reject
    st.markdown("---")
    st.subheader("Approve")
    notes = st.text_input("Notes for this quote (optional)", value=package.reviewer_notes or "")
    if st.button("Approve & finalize", type="primary"):
        _approve(package, run_id, decisions, notes)

    with st.expander("Reject this quote instead"):
        reason = st.text_input("Why are you rejecting it?")
        if st.button("Reject quote"):
            with st.spinner("Recording rejection..."):
                result = finalize_run(run_id, reject=True, reason=reason or notes or "Rejected")
            st.warning(result["message"])
            st.rerun()


def _approve(package: QuotePackage, run_id: str, decisions: dict, notes: str) -> None:
    lines = []
    overrides: list[LineOverride] = []
    for line in package.lines:
        d = decisions[line.line_no]
        extracted = line.extracted.model_copy(update={"quantity": d["qty"]})
        lines.append(line.model_copy(update={"extracted": extracted}))
        if d["kind"] == "use" and d["sku"]:
            overrides.append(LineOverride(
                line_no=line.line_no, action="replace_sku",
                replacement_sku=d["sku"], note=notes or None,
            ))
        elif d["kind"] == "unavailable":
            overrides.append(LineOverride(
                line_no=line.line_no, action="mark_unavailable", note=notes or None,
            ))
        elif d["kind"] == "remove":
            overrides.append(LineOverride(
                line_no=line.line_no, action="remove_line", note=notes or None,
            ))
    updated = package.model_copy(update={
        "lines": lines, "overrides": overrides,
        "approved": True, "reviewer_notes": notes or None,
    })
    _save_package(updated)
    with st.spinner("Finalizing quote, drafting final email, writing ERP record..."):
        result = finalize_run(run_id)
    if result["outcome"] == "blocked":
        st.error(result["message"])
    else:
        st.success(result["message"])
        st.rerun()


def render_outputs(package: QuotePackage, run_id: str) -> None:
    if package.status.value != "approved":
        return
    st.markdown("---")
    st.subheader("Ready to send")

    # Backfill quote/email artifacts for runs approved before they existed, so
    # the quotation always renders instead of showing "(missing)".
    quote = RUNS_DIR / run_id / "quote.md"
    if not quote.exists():
        from rfq_agent.nodes.mocks import write_mocks
        write_mocks(package)
    sent = RUNS_DIR / run_id / "sent_email.txt"
    intacct = RUNS_DIR / run_id / "intacct_payload.json"

    # The customer-facing quotation -- the document the email attaches.
    st.markdown("**Quotation (attached to the email as a PDF)**")
    if quote.exists():
        with st.container(border=True):
            st.markdown(quote.read_text(encoding="utf-8"))
    else:
        st.write("(missing)")
    quote_pdf = RUNS_DIR / run_id / "quote.pdf"
    if quote_pdf.exists():
        st.download_button(
            "Download quotation (PDF)",
            data=quote_pdf.read_bytes(),
            file_name=f"Quotation-{package.quote_id}.pdf",
            mime="application/pdf",
        )

    col = st.columns(2)
    with col[0]:
        st.markdown("**Reply email to the distributor**")
        with st.container(border=True):
            st.text(sent.read_text(encoding="utf-8") if sent.exists() else "(missing)")
        eml = RUNS_DIR / run_id / "sent_email.eml"
        if eml.exists():
            st.download_button(
                "Download email (.eml, quotation attached)",
                data=eml.read_bytes(),
                file_name=f"{run_id}.eml",
                mime="message/rfc822",
            )
    with col[1]:
        st.markdown("**Sage Intacct record (mocked ERP write)**")
        st.caption("Internal system payload -- not shown to the customer.")
        if intacct.exists():
            st.json(json.loads(intacct.read_text(encoding="utf-8")))
        else:
            st.write("(missing)")

    _render_real_send(package, run_id)

    with st.expander("Full details (raw data the agent produced)"):
        st.json(json.loads(package.model_dump_json()))


def _render_real_send(package: QuotePackage, run_id: str) -> None:
    """Optional real Gmail send -- only shown when the user has configured it."""
    from rfq_agent.notify import EmailSendError, gmail_configured, send_via_gmail

    with st.expander("Send this quote for real via Gmail (optional)"):
        if not gmail_configured():
            st.info(
                "Real sending is off. To enable it, set GMAIL_ADDRESS and "
                "GMAIL_APP_PASSWORD (a Gmail App Password) in your .env, then "
                "restart the app. Until then, the mock email above is the "
                "output."
            )
            return
        import os
        default_to = os.environ.get("GMAIL_ADDRESS", "")
        st.caption(
            "The sample RFQ recipient addresses are fictional and will bounce. "
            "Send to your own address to see it land."
        )
        to = st.text_input("Send to", value=default_to, key=f"sendto_{run_id}")
        marker = RUNS_DIR / run_id / "sent_via_gmail.json"
        force = False
        if marker.exists():
            st.warning("This quote was already sent. Tick to resend.")
            force = st.checkbox("Resend anyway", key=f"resend_{run_id}")
        if st.button("Send now", key=f"send_{run_id}"):
            try:
                with st.spinner(f"Sending to {to}..."):
                    rec = send_via_gmail(package, to_override=to or None, force=force)
                st.success(f"Sent to {rec['sent_to']} at {rec['sent_at']}.")
            except EmailSendError as exc:
                st.error(str(exc))


def main() -> None:
    if "active_run" not in st.session_state:
        st.session_state.active_run = None
    render_sidebar()

    run_id = st.session_state.active_run
    if not run_id:
        st.title("RFQ Quote Review")
        st.write(
            "This tool reads an incoming Request for Quote, matches each line to "
            "your product catalog, prices it, and drafts a reply -- then hands it "
            "to you to review and approve."
        )
        st.info("Pick an RFQ on the left and click **Process this RFQ** to begin.")
        return

    package = _load_package(run_id)
    if package is None:
        st.error(f"No quote found for run {run_id}.")
        return
    render_review(package, run_id)


if __name__ == "__main__":
    main()
