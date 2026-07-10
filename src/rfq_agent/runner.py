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

    app = get_compiled_app()
    config = thread_config(run_id)
    snapshot = app.get_state(config)
    if not snapshot or not snapshot.values:
        return {
            "outcome": "blocked",
            "message": (
                f"No checkpoint found for run {run_id}. "
                "Re-run process, then finalize."
            ),
            "mock_paths": {},
        }

    before = snapshot.values.get("package")
    before_subtotal = before.subtotal if before is not None else None
    document = _load_document(run_id)

    if not reject:
        try:
            validate_edits(run_id, reject=False, document=document)
        except (FinalizeBlockedError, FinalizeValidationError) as exc:
            return {
                "outcome": "blocked",
                "message": str(exc),
                "mock_paths": {},
            }

    try:
        app.update_state(
            config,
            {"reject": reject, "reject_reason": reason},
        )
        result = app.invoke(None, config)
    except (FinalizeBlockedError, FinalizeValidationError) as exc:
        return {"outcome": "blocked", "message": str(exc), "mock_paths": {}}
    except Exception as exc:  # noqa: BLE001
        cause: BaseException = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        if isinstance(cause, (FinalizeBlockedError, FinalizeValidationError)):
            return {
                "outcome": "blocked",
                "message": str(cause),
                "mock_paths": {},
            }
        raise

    package = result.get("package")
    if package is None:
        package = _load_package(run_id)
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
