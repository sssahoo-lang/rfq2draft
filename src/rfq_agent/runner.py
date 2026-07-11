"""Shared process/finalize orchestration used by CLI and Streamlit UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rfq_agent.config import RUNS_DIR
from rfq_agent.graph import get_compiled_app, thread_config
from rfq_agent.nodes.validate_edits import (
    FinalizeBlockedError,
    FinalizeValidationError,
    validate_edits,
)
from rfq_agent.schemas import PackageStatus, QuotePackage, RFQDocument


def _load_document(run_id: str) -> RFQDocument | None:
    path = RUNS_DIR / run_id / "document.json"
    if not path.exists():
        return None
    return RFQDocument.model_validate_json(path.read_text(encoding="utf-8"))


def _load_package(run_id: str) -> QuotePackage | None:
    path = RUNS_DIR / run_id / "quote_package.json"
    if not path.exists():
        return None
    return QuotePackage.model_validate_json(path.read_text(encoding="utf-8"))


def process_run(rfq_path: str | Path) -> dict[str, Any]:
    """Run the graph to the human-review interrupt.

    Returns dict with run_id, review_md, package_path.
    Raises FileNotFoundError or RuntimeError on failure.
    """
    path = Path(rfq_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"RFQ file not found: {path}")

    run_id = path.stem
    app = get_compiled_app()
    config = thread_config(run_id)

    # Reprocessing should re-read the RFQ from scratch, not resume a stale
    # checkpoint (e.g. from a prior failed extraction). Clear any existing
    # thread so the graph runs ingest -> extract fresh every time.
    checkpointer = getattr(app, "checkpointer", None)
    if checkpointer is not None and hasattr(checkpointer, "delete_thread"):
        try:
            checkpointer.delete_thread(run_id)
        except Exception:  # noqa: BLE001 -- reset is best-effort
            pass

    app.invoke({"rfq_path": str(path), "run_id": run_id}, config)

    review_md = RUNS_DIR / run_id / "review.md"
    package_path = RUNS_DIR / run_id / "quote_package.json"
    return {
        "run_id": run_id,
        "review_md": str(review_md),
        "package_path": str(package_path),
    }


def finalize_run(
    run_id: str,
    *,
    reject: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Resume a paused run after human review.

    Returns dict with outcome ("approved" | "rejected" | "blocked"),
    message, package fields, and mock paths when applicable.
    """
    package_path = RUNS_DIR / run_id / "quote_package.json"
    if not package_path.exists():
        return {
            "outcome": "blocked",
            "message": (
                f"No paused run found for {run_id}. "
                f"Expected {package_path}. Run process first."
            ),
            "mock_paths": {},
        }

    before_pkg = _load_package(run_id)
    before_subtotal = before_pkg.subtotal if before_pkg is not None else None
    document = _load_document(run_id)

    app = get_compiled_app()
    config = thread_config(run_id)
    snapshot = app.get_state(config)
    # Resumable only if the graph is paused mid-run (has pending next nodes).
    # A terminal/absent checkpoint (reopened, reset, or lost) is not resumable;
    # in that case finalize directly from the on-disk package instead.
    resumable = bool(snapshot and snapshot.values and snapshot.next)

    try:
        if resumable:
            if not reject:
                validate_edits(run_id, reject=False, document=document)
            app.update_state(config, {"reject": reject, "reject_reason": reason})
            result = app.invoke(None, config)
            package = result.get("package") or _load_package(run_id)
        else:
            # Disk fallback: complete finalize without the graph. Same nodes,
            # called directly on the on-disk package.
            package = validate_edits(
                run_id, reject=reject, reject_reason=reason, document=document
            )
            if package.status == PackageStatus.approved:
                from rfq_agent.nodes.mocks import write_mocks

                write_mocks(package)
    except (FinalizeBlockedError, FinalizeValidationError) as exc:
        return {"outcome": "blocked", "message": str(exc), "mock_paths": {}}
    except Exception as exc:  # noqa: BLE001
        cause: BaseException = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        if isinstance(cause, (FinalizeBlockedError, FinalizeValidationError)):
            return {"outcome": "blocked", "message": str(cause), "mock_paths": {}}
        raise

    if package is None:
        return {
            "outcome": "blocked",
            "message": "Finalize finished without a package.",
            "mock_paths": {},
        }

    if package.status == PackageStatus.rejected:
        return {
            "outcome": "rejected",
            "message": package.reviewer_notes or reason or "(none)",
            "run_id": run_id,
            "subtotal": str(package.subtotal),
            "before_subtotal": (
                str(before_subtotal) if before_subtotal is not None else None
            ),
            "overrides": [
                {
                    "line_no": ov.line_no,
                    "action": ov.action,
                    "replacement_sku": ov.replacement_sku,
                }
                for ov in package.overrides
            ],
            "mock_paths": {},
            "package_path": str(package_path),
        }

    sent = RUNS_DIR / run_id / "sent_email.txt"
    intacct = RUNS_DIR / run_id / "intacct_payload.json"
    return {
        "outcome": "approved",
        "message": f"Approved run {run_id}.",
        "run_id": run_id,
        "subtotal": str(package.subtotal),
        "before_subtotal": (
            str(before_subtotal) if before_subtotal is not None else None
        ),
        "overrides": [
            {
                "line_no": ov.line_no,
                "action": ov.action,
                "replacement_sku": ov.replacement_sku,
            }
            for ov in package.overrides
        ],
        "mock_paths": {
            "sent_email": str(sent),
            "intacct_payload": str(intacct),
        },
        "package_path": str(package_path),
    }
