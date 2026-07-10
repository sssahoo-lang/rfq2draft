"""CLI: process | finalize"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from rfq_agent.config import RUNS_DIR
from rfq_agent.graph import get_compiled_app, thread_config
from rfq_agent.nodes.validate_edits import (
    FinalizeBlockedError,
    FinalizeValidationError,
    validate_edits,
)
from rfq_agent.schemas import PackageStatus, RFQDocument


def _die(message: str, *, debug: bool = False, exc: BaseException | None = None) -> None:
    print(message, file=sys.stderr)
    if debug and exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    sys.exit(1)


def _load_document(run_id: str) -> RFQDocument | None:
    path = RUNS_DIR / run_id / "document.json"
    if not path.exists():
        return None
    return RFQDocument.model_validate_json(path.read_text(encoding="utf-8"))


def cmd_process(rfq_path: str, *, debug: bool = False) -> None:
    path = Path(rfq_path).resolve()
    if not path.exists():
        _die(f"RFQ file not found: {path}")

    run_id = path.stem
    app = get_compiled_app()
    config = thread_config(run_id)

    try:
        app.invoke({"rfq_path": str(path), "run_id": run_id}, config)
    except Exception as exc:  # noqa: BLE001
        _die(f"Processing failed: {exc}", debug=debug, exc=exc)

    review = RUNS_DIR / run_id / "review.md"
    package = RUNS_DIR / run_id / "quote_package.json"
    print(f"run_id: {run_id}")
    print(f"review: {review}")
    print(f"package: {package}")
    print(f"Next: python -m rfq_agent finalize {run_id}")
    print(
        f"(Edit {package} first if any lines need overrides; "
        f"set approved to true when ready.)"
    )


def cmd_finalize(
    run_id: str,
    *,
    reject: bool = False,
    reason: str | None = None,
    debug: bool = False,
) -> None:
    package_path = RUNS_DIR / run_id / "quote_package.json"
    if not package_path.exists():
        _die(
            f"No paused run found for {run_id}. "
            f"Expected {package_path}. Run process first."
        )

    app = get_compiled_app()
    config = thread_config(run_id)
    snapshot = app.get_state(config)
    if not snapshot or not snapshot.values:
        _die(
            f"No checkpoint found for run {run_id}. "
            "Re-run process, then finalize."
        )

    before = snapshot.values.get("package")
    before_subtotal = before.subtotal if before is not None else None
    document = _load_document(run_id)

    # Pre-validate on disk BEFORE resuming the graph so a blocked gate leaves
    # the interrupt checkpoint intact for a later retry.
    if not reject:
        try:
            validate_edits(run_id, reject=False, document=document)
        except FinalizeBlockedError as exc:
            _die(str(exc), debug=debug, exc=exc)
        except FinalizeValidationError as exc:
            _die(str(exc), debug=debug, exc=exc)

    try:
        app.update_state(
            config,
            {"reject": reject, "reject_reason": reason},
        )
        result = app.invoke(None, config)
    except FinalizeBlockedError as exc:
        _die(str(exc), debug=debug, exc=exc)
    except FinalizeValidationError as exc:
        _die(str(exc), debug=debug, exc=exc)
    except Exception as exc:  # noqa: BLE001
        cause: BaseException = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        if isinstance(cause, (FinalizeBlockedError, FinalizeValidationError)):
            _die(str(cause), debug=debug, exc=exc)
        _die(f"Finalize failed: {exc}", debug=debug, exc=exc)

    package = result.get("package")
    if package is None:
        # Fall back to disk (reject / end path)
        if package_path.exists():
            from rfq_agent.schemas import QuotePackage

            package = QuotePackage.model_validate_json(
                package_path.read_text(encoding="utf-8")
            )
        else:
            _die("Finalize finished without a package.")

    if package.status == PackageStatus.rejected:
        print(f"Rejected run {run_id}.")
        print(f"Reason: {package.reviewer_notes or reason or '(none)'}")
        print(f"Package written: {package_path}")
        print("No email sent. No Intacct payload written.")
        return

    print(f"Approved run {run_id}.")
    if before_subtotal is not None and package.subtotal != before_subtotal:
        print(f"Subtotal changed: {before_subtotal} -> {package.subtotal}")
    else:
        print(f"Subtotal: {package.subtotal}")
    if package.overrides:
        print("Overrides applied:")
        for ov in package.overrides:
            print(
                f"  - line {ov.line_no}: {ov.action}"
                + (f" -> {ov.replacement_sku}" if ov.replacement_sku else "")
            )
    else:
        print("No overrides.")

    sent = RUNS_DIR / run_id / "sent_email.txt"
    intacct = RUNS_DIR / run_id / "intacct_payload.json"
    print(f"sent_email: {sent}")
    print(f"intacct_payload: {intacct}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="rfq_agent",
        description="RFQ-to-quote drafting agent (process / finalize)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show full tracebacks on errors",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_process = sub.add_parser("process", help="Run pipeline to human review gate")
    p_process.add_argument("rfq_path", help="Path to RFQ PDF or .eml")

    p_final = sub.add_parser("finalize", help="Resume after human review")
    p_final.add_argument("run_id", help="Run id from process (filename stem)")
    p_final.add_argument(
        "--reject",
        action="store_true",
        help="Reject the quote without sending mocks",
    )
    p_final.add_argument(
        "--reason",
        default=None,
        help="Rejection reason (with --reject)",
    )

    args = parser.parse_args(argv)
    if args.command == "process":
        cmd_process(args.rfq_path, debug=args.debug)
    elif args.command == "finalize":
        cmd_finalize(
            args.run_id,
            reject=args.reject,
            reason=args.reason,
            debug=args.debug,
        )
    else:
        _die(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
