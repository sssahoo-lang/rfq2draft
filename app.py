"""Streamlit review UI -- thin window over runner + runs/<id>/ files."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import streamlit as st

from rfq_agent.config import RFQS_DIR, RUNS_DIR
from rfq_agent.runner import finalize_run, process_run
from rfq_agent.schemas import LineOverride, QuotePackage

st.set_page_config(page_title="RFQ Quote Review", layout="wide")


def _rfq_files() -> list[Path]:
    return sorted(
        p for p in RFQS_DIR.iterdir() if p.suffix.lower() in {".pdf", ".eml"}
    )


def _existing_runs() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in RUNS_DIR.iterdir()
        if d.is_dir() and (d / "quote_package.json").exists()
    )


def _load_package(run_id: str) -> QuotePackage | None:
    path = RUNS_DIR / run_id / "quote_package.json"
    if not path.exists():
        return None
    return QuotePackage.model_validate_json(path.read_text(encoding="utf-8"))


def _save_package(package: QuotePackage) -> None:
    path = RUNS_DIR / package.run_id / "quote_package.json"
    path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _is_flagged(line) -> bool:
    return line.match.needs_human_review or line.unit_price is None


def _money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def main() -> None:
    st.title("RFQ Quote Review")
    rfqs = _rfq_files()
    runs = _existing_runs()

    with st.sidebar:
        st.header("Source")
        labels = [p.name for p in rfqs] + [f"[run] {r}" for r in runs]
        choice = st.radio("RFQs / existing runs", labels, index=0)
        if choice.startswith("[run] "):
            selected_run = choice.removeprefix("[run] ")
            selected_rfq = None
        else:
            selected_rfq = next(p for p in rfqs if p.name == choice)
            selected_run = selected_rfq.stem

        if "active_run" not in st.session_state:
            st.session_state.active_run = None

        if selected_rfq is not None:
            existing = _load_package(selected_run)
            if existing is None:
                if st.button("Process RFQ", type="primary"):
                    with st.spinner("Processing RFQ (extraction + email)..."):
                        result = process_run(selected_rfq)
                    st.session_state.active_run = result["run_id"]
                    st.rerun()
            else:
                st.info(f"Run exists: {selected_run}")
                if st.button("Load existing run"):
                    st.session_state.active_run = selected_run
                    st.rerun()
                if st.button("Reprocess from scratch"):
                    if st.session_state.get("confirm_reprocess") != selected_run:
                        st.session_state.confirm_reprocess = selected_run
                        st.warning("Click again to confirm reprocess.")
                    else:
                        with st.spinner("Reprocessing..."):
                            result = process_run(selected_rfq)
                        st.session_state.active_run = result["run_id"]
                        st.session_state.confirm_reprocess = None
                        st.rerun()
        else:
            if st.button("Load run"):
                st.session_state.active_run = selected_run
                st.rerun()

    run_id = st.session_state.active_run
    if not run_id:
        st.write("Select an RFQ and click **Process RFQ** (or load an existing run).")
        return

    package = _load_package(run_id)
    if package is None:
        st.error(f"No package for run {run_id}")
        return

    done = package.status.value in {"approved", "rejected"}
    flagged = [ln for ln in package.lines if _is_flagged(ln)]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customer", package.customer_name or "-")
    c2.metric("Quote", package.quote_id)
    c3.metric("Subtotal", _money(package.subtotal))
    c4.metric("Status", package.status.value)
    st.caption(
        f"RFQ date: {package.rfq_date or '-'} | "
        f"Needed by: {package.needed_by or '-'} | Run: {run_id}"
    )

    if flagged and not done:
        st.warning("\n".join(package.flag_summary))
    else:
        st.success(package.flag_summary[0] if package.flag_summary else "Ready")

    edits: dict[int, dict] = {}
    for line in package.lines:
        is_flag = _is_flagged(line)
        with st.expander(
            f"Line {line.line_no}: {line.match.status.value}"
            + (" [NEEDS DECISION]" if is_flag else ""),
            expanded=is_flag and not done,
        ):
            st.markdown(f"> {line.source_text}")
            st.write(
                f"SKU `{line.extracted.sku}` | qty {line.extracted.quantity} "
                f"{line.extracted.uom} | conf {line.extracted.extraction_confidence}"
            )
            st.write(line.match.rationale)
            if line.unit_price is not None:
                st.write(
                    f"Price: {_money(line.unit_price)} / {line.uom} | "
                    f"Extended {_money(line.extended_price)}"
                )
            else:
                st.error("NOT PRICED - needs your decision")
            qty = st.number_input(
                f"Quantity L{line.line_no}",
                min_value=0.01,
                value=float(line.extracted.quantity),
                key=f"qty_{run_id}_{line.line_no}",
                disabled=done,
            )
            decision = {"qty": Decimal(str(qty)), "action": None, "sku": None}
            if is_flag and line.match.candidates:
                rows = [
                    {
                        "sku": c.sku,
                        "score": round(c.score, 3),
                        "breakdown": "; ".join(
                            f"{k}: {v}" for k, v in c.breakdown.items()
                        ),
                    }
                    for c in line.match.candidates
                ]
                st.dataframe(rows, use_container_width=True)
                skus = [c.sku for c in line.match.candidates]
                default = line.match.matched_sku if line.match.matched_sku in skus else skus[0]
                pick = st.selectbox(
                    f"Candidate L{line.line_no}",
                    skus,
                    index=skus.index(default),
                    key=f"cand_{run_id}_{line.line_no}",
                    disabled=done,
                )
                action = st.radio(
                    f"Action L{line.line_no}",
                    ["accept_suggested", "replace_sku", "remove_line"],
                    key=f"act_{run_id}_{line.line_no}",
                    disabled=done,
                    horizontal=True,
                )
                decision["action"] = action
                decision["sku"] = pick
            edits[line.line_no] = decision

    notes = st.text_input("Reviewer notes", value=package.reviewer_notes or "", disabled=done)
    reject_reason = st.text_input("Reject reason", disabled=done)
    a1, a2 = st.columns(2)
    if a1.button("Approve & Finalize", type="primary", disabled=done):
        lines = []
        overrides: list[LineOverride] = []
        for line in package.lines:
            ed = edits[line.line_no]
            extracted = line.extracted.model_copy(update={"quantity": ed["qty"]})
            lines.append(line.model_copy(update={"extracted": extracted}))
            if ed["action"]:
                overrides.append(
                    LineOverride(
                        line_no=line.line_no,
                        action=ed["action"],
                        replacement_sku=(
                            ed["sku"] if ed["action"] == "replace_sku" else None
                        ),
                        note=notes or None,
                    )
                )
        updated = package.model_copy(
            update={
                "lines": lines,
                "overrides": overrides,
                "approved": True,
                "reviewer_notes": notes or None,
            }
        )
        _save_package(updated)
        with st.spinner("Finalizing..."):
            result = finalize_run(run_id)
        if result["outcome"] == "blocked":
            st.error(result["message"])
        else:
            st.success(result["message"])
            st.rerun()

    if a2.button("Reject", disabled=done):
        with st.spinner("Rejecting..."):
            result = finalize_run(
                run_id, reject=True, reason=reject_reason or notes or "Rejected"
            )
        st.warning(result["message"])
        st.rerun()

    if package.status.value == "approved":
        sent = RUNS_DIR / run_id / "sent_email.txt"
        intacct = RUNS_DIR / run_id / "intacct_payload.json"
        with st.expander("Sent email", expanded=True):
            st.text(sent.read_text(encoding="utf-8") if sent.exists() else "(missing)")
        with st.expander("Sage Intacct payload", expanded=True):
            if intacct.exists():
                st.json(json.loads(intacct.read_text(encoding="utf-8")))
            else:
                st.write("(missing)")
    with st.expander("What the agent did (raw package JSON)"):
        st.json(json.loads(package.model_dump_json()))


if __name__ == "__main__":
    main()
