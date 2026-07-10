"""CLI: process | finalize (thin wrapper over runner)."""

from __future__ import annotations

import argparse
import sys
import traceback

from rfq_agent.runner import finalize_run, process_run


def _die(message: str, *, debug: bool = False, exc: BaseException | None = None) -> None:
    print(message, file=sys.stderr)
    if debug and exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    sys.exit(1)


def cmd_process(rfq_path: str, *, debug: bool = False) -> None:
    try:
        result = process_run(rfq_path)
    except FileNotFoundError as exc:
        _die(str(exc), debug=debug, exc=exc)
    except Exception as exc:  # noqa: BLE001
        _die(f"Processing failed: {exc}", debug=debug, exc=exc)

    run_id = result["run_id"]
    print(f"run_id: {run_id}")
    print(f"review: {result['review_md']}")
    print(f"package: {result['package_path']}")
    print(f"Next: python -m rfq_agent finalize {run_id}")
    print(
        f"(Edit {result['package_path']} first if any lines need overrides; "
        f"set approved to true when ready.)"
    )


def cmd_finalize(
    run_id: str,
    *,
    reject: bool = False,
    reason: str | None = None,
    debug: bool = False,
) -> None:
    try:
        result = finalize_run(run_id, reject=reject, reason=reason)
    except Exception as exc:  # noqa: BLE001
        _die(f"Finalize failed: {exc}", debug=debug, exc=exc)

    outcome = result["outcome"]
    if outcome == "blocked":
        _die(result["message"], debug=debug)

    if outcome == "rejected":
        print(f"Rejected run {run_id}.")
        print(f"Reason: {result['message']}")
        print(f"Package written: {result.get('package_path')}")
        print("No email sent. No Intacct payload written.")
        return

    print(result["message"])
    before = result.get("before_subtotal")
    after = result.get("subtotal")
    if before is not None and after is not None and before != after:
        print(f"Subtotal changed: {before} -> {after}")
    else:
        print(f"Subtotal: {after}")
    overrides = result.get("overrides") or []
    if overrides:
        print("Overrides applied:")
        for ov in overrides:
            extra = f" -> {ov['replacement_sku']}" if ov.get("replacement_sku") else ""
            print(f"  - line {ov['line_no']}: {ov['action']}{extra}")
    else:
        print("No overrides.")
    mocks = result.get("mock_paths") or {}
    print(f"sent_email: {mocks.get('sent_email')}")
    print(f"intacct_payload: {mocks.get('intacct_payload')}")


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
