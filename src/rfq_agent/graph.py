"""LangGraph state machine wiring (7 nodes, interrupt, SqliteSaver)."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from rfq_agent.catalog.loader import load_catalog_index
from rfq_agent.config import CATALOG_CSV, RUNS_DIR
from rfq_agent.nodes import assemble as assemble_mod
from rfq_agent.nodes import enrich as enrich_mod
from rfq_agent.nodes import extract as extract_mod
from rfq_agent.nodes import ingest as ingest_mod
from rfq_agent.nodes import match as match_mod
from rfq_agent.nodes import mocks as mocks_mod
from rfq_agent.nodes import validate_edits as validate_mod
from rfq_agent.schemas import (
    ExtractedRFQ,
    MatchResult,
    PackageStatus,
    QuoteLine,
    QuotePackage,
    RFQDocument,
)


class GraphState(TypedDict, total=False):
    """Accumulated RFQ pipeline artifacts for one run."""

    run_id: str
    rfq_path: str
    document: RFQDocument
    extracted: ExtractedRFQ
    matches: list[MatchResult]
    quote_lines: list[QuoteLine]
    subtotal: Decimal
    package: QuotePackage
    error: str | None
    retry_count: int
    reject: bool
    reject_reason: str | None
    mock_paths: dict[str, str]


def node_ingest(state: GraphState) -> dict[str, Any]:
    document = ingest_mod.ingest(state["rfq_path"])
    return {"document": document, "run_id": document.run_id, "error": None}


def node_extract(state: GraphState) -> dict[str, Any]:
    try:
        extracted = extract_mod.extract(state["document"])
        return {"extracted": extracted, "error": None}
    except Exception as exc:  # noqa: BLE001 -- routed to fail node
        return {"error": str(exc)}


def node_fail(state: GraphState) -> dict[str, Any]:
    run_id = state.get("run_id") or Path(state.get("rfq_path", "unknown")).stem
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    message = state.get("error") or "Unknown extraction failure"
    (run_dir / "error.txt").write_text(message + "\n", encoding="utf-8")
    raise RuntimeError(
        f"Extraction failed for run {run_id}. Details written to "
        f"{run_dir / 'error.txt'}: {message}"
    )


def node_match(state: GraphState) -> dict[str, Any]:
    index = load_catalog_index(CATALOG_CSV)
    matches = match_mod.match_lines(state["extracted"], index)
    return {"matches": matches}


def node_enrich(state: GraphState) -> dict[str, Any]:
    index = load_catalog_index(CATALOG_CSV)
    quote_lines, subtotal = enrich_mod.enrich(
        state["extracted"], state["matches"], index
    )
    return {"quote_lines": quote_lines, "subtotal": subtotal}


def node_assemble(state: GraphState) -> dict[str, Any]:
    index = load_catalog_index(CATALOG_CSV)
    package = assemble_mod.assemble(
        state["document"],
        state["extracted"],
        state["quote_lines"],
        state["subtotal"],
        index,
    )
    return {"package": package}


def node_validate_edits(state: GraphState) -> dict[str, Any]:
    package = validate_mod.validate_edits(
        state["run_id"],
        reject=bool(state.get("reject")),
        reject_reason=state.get("reject_reason"),
        document=state.get("document"),
    )
    return {"package": package}


def node_mocks(state: GraphState) -> dict[str, Any]:
    paths = mocks_mod.write_mocks(state["package"])
    return {"mock_paths": {k: str(v) for k, v in paths.items()}}


def _route_after_extract(state: GraphState) -> str:
    if state.get("error"):
        return "fail"
    return "match"


def _route_after_validate(state: GraphState) -> str:
    package = state.get("package")
    if package is not None and package.status == PackageStatus.approved:
        return "mocks"
    return "end"


def build_graph() -> StateGraph:
    """Construct the uncompiled StateGraph (no side effects)."""
    graph = StateGraph(GraphState)
    graph.add_node("ingest", node_ingest)
    graph.add_node("extract", node_extract)
    graph.add_node("fail", node_fail)
    graph.add_node("match", node_match)
    graph.add_node("enrich", node_enrich)
    graph.add_node("assemble", node_assemble)
    graph.add_node("validate_edits", node_validate_edits)
    graph.add_node("mocks", node_mocks)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "extract")
    graph.add_conditional_edges(
        "extract",
        _route_after_extract,
        {"fail": "fail", "match": "match"},
    )
    graph.add_edge("match", "enrich")
    graph.add_edge("enrich", "assemble")
    # interrupt_after assemble is set at compile time
    graph.add_edge("assemble", "validate_edits")
    graph.add_conditional_edges(
        "validate_edits",
        _route_after_validate,
        {"mocks": "mocks", "end": END},
    )
    graph.add_edge("mocks", END)
    return graph


def get_compiled_app():
    """Compile graph with SqliteSaver. Call from CLI, not at import time."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    db_path = RUNS_DIR / "checkpoints.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    # Allow Pydantic schema types in checkpoint msgpack (LangGraph 1.x).
    if hasattr(saver, "serde") and hasattr(saver.serde, "allowed_msgpack_modules"):
        saver.serde.allowed_msgpack_modules.extend(
            [
                ("rfq_agent.schemas", "SourceType"),
                ("rfq_agent.schemas", "RFQDocument"),
                ("rfq_agent.schemas", "UOM"),
                ("rfq_agent.schemas", "LineAttributes"),
                ("rfq_agent.schemas", "ExtractedLine"),
                ("rfq_agent.schemas", "ExtractedRFQ"),
                ("rfq_agent.schemas", "MatchStatus"),
                ("rfq_agent.schemas", "MatchCandidate"),
                ("rfq_agent.schemas", "MatchResult"),
                ("rfq_agent.schemas", "QuoteLine"),
                ("rfq_agent.schemas", "LineOverride"),
                ("rfq_agent.schemas", "PackageStatus"),
                ("rfq_agent.schemas", "QuotePackage"),
            ]
        )
    saver.setup()
    return build_graph().compile(
        checkpointer=saver,
        interrupt_after=["assemble"],
    )


def thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}
