#!/usr/bin/env python3
"""Verify LangGraph interrupt + finalize recompute + override gate + mocks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from rfq_agent.config import PROJECT_ROOT, RFQS_DIR, RUNS_DIR
from rfq_agent.nodes.assemble import numeric_guard_ok
from rfq_agent.nodes import extract as extract_mod
from rfq_agent.schemas import QuotePackage

PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")


def _pass(name: str) -> None:
    print(f"PASS: {name}")


def _fail(name: str, detail: str) -> None:
    print(f"FAIL: {name} -- {detail}")
    raise AssertionError(f"{name}: {detail}")


def _clean_run(run_id: str) -> None:
    path = RUNS_DIR / run_id
    if path.exists():
        shutil.rmtree(path)
    # Also clear this thread from checkpoints by deleting db if present at start
    # (full db wipe once at script start).


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "rfq_agent", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )


def _load_package(run_id: str) -> QuotePackage:
    return QuotePackage.model_validate_json(
        (RUNS_DIR / run_id / "quote_package.json").read_text(encoding="utf-8")
    )


def _save_package(run_id: str, package: QuotePackage) -> None:
    (RUNS_DIR / run_id / "quote_package.json").write_text(
        package.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def journey_a() -> None:
    name = "A HAPPY EDIT (RFQ-001)"
    run_id = "RFQ-001_CarolinaFluidPower"
    rfq = RFQS_DIR / "RFQ-001_CarolinaFluidPower.pdf"
    _clean_run(run_id)

    extract_calls = {"n": 0}
    real_extract = extract_mod.extract

    def counting_extract(document):
        extract_calls["n"] += 1
        return real_extract(document)

    extract_mod.extract = counting_extract  # type: ignore[assignment]
    try:
        # process via in-process graph so the counter is visible
        from rfq_agent.graph import get_compiled_app, thread_config

        app = get_compiled_app()
        app.invoke(
            {"rfq_path": str(rfq.resolve()), "run_id": run_id},
            thread_config(run_id),
        )
        assert extract_calls["n"] == 1, f"extract calls after process={extract_calls['n']}"

        package = _load_package(run_id)
        # Edit line 1 quantity 500 -> 600
        lines = []
        for line in package.lines:
            if line.line_no == 1:
                extracted = line.extracted.model_copy(update={"quantity": Decimal("600")})
                lines.append(line.model_copy(update={"extracted": extracted}))
            else:
                lines.append(line)
        package = package.model_copy(update={"lines": lines, "approved": True})
        _save_package(run_id, package)

        calls_before_finalize = extract_calls["n"]
        app.update_state(thread_config(run_id), {"reject": False})
        result = app.invoke(None, thread_config(run_id))
        assert extract_calls["n"] == calls_before_finalize, (
            f"extract re-ran on finalize: {extract_calls['n']} vs {calls_before_finalize}"
        )

        final = _load_package(run_id)
        line1 = next(ln for ln in final.lines if ln.line_no == 1)
        assert line1.extended_price == Decimal("5970.00"), line1.extended_price
        assert final.subtotal == Decimal("10067.50"), final.subtotal
        assert final.status.value == "approved"

        sent = RUNS_DIR / run_id / "sent_email.txt"
        intacct = RUNS_DIR / run_id / "intacct_payload.json"
        assert sent.exists(), "missing sent_email.txt"
        assert intacct.exists(), "missing intacct_payload.json"
        payload = json.loads(intacct.read_text(encoding="utf-8"))
        assert len(payload["lines"]) == 6, payload["lines"]
        assert payload["idempotency_key"] == final.quote_id

        ok, detail = numeric_guard_ok(final.email_body or "", final.subtotal)
        assert ok, detail
        print(
            f"  extract_calls={extract_calls['n']} "
            f"line1_ext={line1.extended_price} subtotal={final.subtotal}"
        )
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))
    finally:
        extract_mod.extract = real_extract  # type: ignore[assignment]


def journey_b() -> None:
    name = "B FLAGGED LINE (RFQ-003)"
    run_id = "RFQ-003_PiedmontHydraulics"
    rfq = RFQS_DIR / "RFQ-003_PiedmontHydraulics.pdf"
    _clean_run(run_id)

    try:
        proc = _run_cli(["process", str(rfq)])
        assert proc.returncode == 0, proc.stderr or proc.stdout

        package = _load_package(run_id)
        package = package.model_copy(update={"approved": True, "overrides": []})
        _save_package(run_id, package)

        blocked = _run_cli(["finalize", run_id])
        assert blocked.returncode != 0, "expected finalize to block"
        msg = (blocked.stderr or "") + (blocked.stdout or "")
        assert "line 6" in msg.lower(), msg
        assert not (RUNS_DIR / run_id / "sent_email.txt").exists()
        assert not (RUNS_DIR / run_id / "intacct_payload.json").exists()
        print(f"  blocked as expected: {msg.strip()[:200]}")

        from rfq_agent.schemas import LineOverride

        package = _load_package(run_id)
        package = package.model_copy(
            update={
                "approved": True,
                "overrides": [
                    LineOverride(
                        line_no=6,
                        action="replace_sku",
                        replacement_sku="SHF-PTFE-025",
                        note="Buyer confirmed PTFE / chemical service",
                    )
                ],
            }
        )
        _save_package(run_id, package)

        last_exc = None
        for attempt in range(2):
            try:
                ok = _run_cli(["finalize", run_id])
                assert ok.returncode == 0, ok.stderr or ok.stdout
                final = _load_package(run_id)
                line6 = next(ln for ln in final.lines if ln.line_no == 6)
                assert line6.unit_price == Decimal("12.40"), line6.unit_price
                assert line6.extended_price == Decimal("1860.00"), line6.extended_price
                assert final.subtotal == Decimal("8742.25"), final.subtotal
                body = (final.email_body or "").lower()
                attention = final.flag_summary[0] if final.flag_summary else ""
                assert "all " in attention.lower() and "priced cleanly" in attention.lower(), (
                    attention
                )
                assert "could you confirm whether ptfe" not in body
                assert "chemical-rated hose is required" not in body
                assert (RUNS_DIR / run_id / "sent_email.txt").exists()
                assert (RUNS_DIR / run_id / "intacct_payload.json").exists()
                print(
                    f"  line6_ext={line6.extended_price} subtotal={final.subtotal} "
                    f"flag0={attention!r}"
                )
                _pass(name)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                print(f"  FAIL attempt {attempt + 1}: {exc}")
                if attempt == 0:
                    print("  retrying once (flakiness protocol)...")
        raise AssertionError(str(last_exc))
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def journey_c() -> None:
    name = "C REJECT (RFQ-004)"
    run_id = "RFQ-004_DeltaPower"
    rfq = RFQS_DIR / "RFQ-004_DeltaPower.eml"
    _clean_run(run_id)

    try:
        proc = _run_cli(["process", str(rfq)])
        assert proc.returncode == 0, proc.stderr or proc.stdout

        rejected = _run_cli(
            [
                "finalize",
                run_id,
                "--reject",
                "--reason",
                "customer pricing under negotiation",
            ]
        )
        assert rejected.returncode == 0, rejected.stderr or rejected.stdout
        final = _load_package(run_id)
        assert final.status.value == "rejected", final.status
        assert "customer pricing under negotiation" in (final.reviewer_notes or "")
        assert not (RUNS_DIR / run_id / "sent_email.txt").exists()
        assert not (RUNS_DIR / run_id / "intacct_payload.json").exists()
        print(f"  status={final.status.value} notes={final.reviewer_notes!r}")
        _pass(name)
    except Exception as exc:  # noqa: BLE001
        _fail(name, str(exc))


def main() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    # Fresh checkpoint DB so thread ids from prior runs do not collide.
    ckpt = RUNS_DIR / "checkpoints.db"
    if ckpt.exists():
        ckpt.unlink()
    for run_id in (
        "RFQ-001_CarolinaFluidPower",
        "RFQ-003_PiedmontHydraulics",
        "RFQ-004_DeltaPower",
    ):
        _clean_run(run_id)

    journey_a()
    journey_b()
    journey_c()
    print("\nall roundtrip checks passed")


if __name__ == "__main__":
    main()
