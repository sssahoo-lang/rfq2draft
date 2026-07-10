"""Extract node: LLM #1 structured line-item extraction."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import anthropic
from pydantic import ValidationError

from rfq_agent.config import (
    ANTHROPIC_MODEL,
    EXTRACTION_MAX_RETRIES,
    MIN_PDF_TEXT_CHARS,
    RUNS_DIR,
    SKU_PATTERN,
)
from rfq_agent.schemas import ExtractedLine, ExtractedRFQ, RFQDocument, SourceType

TOOL_NAME = "record_rfq"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "extraction.md"


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and set "
            "ANTHROPIC_API_KEY, or export it in your shell."
        )
    return key


def _tool_input_schema() -> dict:
    """JSON Schema for the record_rfq tool, derived from ExtractedRFQ."""
    schema = ExtractedRFQ.model_json_schema()
    # Anthropic tools expect a top-level object schema.
    schema["type"] = "object"
    return schema


def _build_user_text(document: RFQDocument) -> str:
    parts: list[str] = []
    if document.source_type == SourceType.eml:
        parts.append("Email headers:")
        parts.append(f"From: {document.sender or ''}")
        parts.append(f"Subject: {document.subject or ''}")
        parts.append(f"Date: {document.sent_date or ''}")
        parts.append("")
        parts.append("Email body:")
    else:
        parts.append("RFQ document text:")
    parts.append(document.raw_text)
    return "\n".join(parts)


def _use_multimodal_pdf(document: RFQDocument) -> bool:
    """True when PDF text is too thin; send PDF bytes instead.

    Untested by the sample data (both sample PDFs extract cleanly via pypdf).
    """
    return (
        document.source_type == SourceType.pdf
        and len(document.raw_text) < MIN_PDF_TEXT_CHARS
    )


def _user_content(document: RFQDocument) -> list[dict] | str:
    if _use_multimodal_pdf(document):
        pdf_path = Path(document.source_path)
        pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {
                "type": "text",
                "text": (
                    "Extract the RFQ into the record_rfq tool. "
                    "PDF text extraction was too thin; use the attached document."
                ),
            },
        ]
    return _build_user_text(document)


def _tool_input_from_response(message: anthropic.types.Message) -> dict:
    for block in message.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return block.input
    raise RuntimeError(
        f"Claude response did not include a {TOOL_NAME} tool call: {message.content!r}"
    )


def _stringify_decimals(payload: dict) -> dict:
    """Convert JSON numbers on Decimal fields to strings before Pydantic validate.

    schemas.py rejects float for money/Decimal fields; tool JSON often emits numbers.
    """
    data = json.loads(json.dumps(payload))  # deep copy via JSON
    for line in data.get("lines") or []:
        if isinstance(line.get("quantity"), (int, float)):
            line["quantity"] = str(line["quantity"])
        attrs = line.get("attributes") or {}
        if isinstance(attrs.get("length_ft"), (int, float)):
            attrs["length_ft"] = str(attrs["length_ft"])
        line["attributes"] = attrs
    return data


def _post_validate(extracted: ExtractedRFQ) -> ExtractedRFQ:
    """Deterministic cleanup after LLM structured output."""
    cleaned_lines: list[ExtractedLine] = []
    for line in extracted.lines:
        sku = line.sku
        notes = line.notes
        if sku is not None and not SKU_PATTERN.fullmatch(sku.strip()):
            note_bit = "sku-like text did not match expected pattern"
            notes = f"{notes}; {note_bit}" if notes else note_bit
            sku = None
        cleaned_lines.append(line.model_copy(update={"sku": sku, "notes": notes}))
    return extracted.model_copy(update={"lines": cleaned_lines})


def extract(document: RFQDocument) -> ExtractedRFQ:
    """LLM extraction call site #1: RFQDocument -> ExtractedRFQ."""
    api_key = _require_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    system = _load_system_prompt()
    tools = [
        {
            "name": TOOL_NAME,
            "description": (
                "Record the fully extracted RFQ header and line items. "
                "Call this once with the complete extraction."
            ),
            "input_schema": _tool_input_schema(),
        }
    ]
    tool_choice = {"type": "tool", "name": TOOL_NAME}

    messages: list[dict] = [
        {"role": "user", "content": _user_content(document)},
    ]

    last_error: str | None = None
    attempts = 1 + EXTRACTION_MAX_RETRIES
    for attempt in range(attempts):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            temperature=0,
            system=system,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        tool_input = _tool_input_from_response(response)
        try:
            extracted = ExtractedRFQ.model_validate(_stringify_decimals(tool_input))
            extracted = _post_validate(extracted)
            out_path = RUNS_DIR / document.run_id / "extracted.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                extracted.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            return extracted
        except (ValidationError, TypeError, ValueError) as exc:
            last_error = str(exc)
            if attempt + 1 >= attempts:
                break
            # Retry once: append assistant tool call + user correction request.
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The previous {TOOL_NAME} call failed validation:\n"
                        f"{last_error}\n\n"
                        f"Call {TOOL_NAME} again with a corrected payload that "
                        "satisfies the schema."
                    ),
                }
            )

    raise RuntimeError(
        f"Extraction failed after {attempts} attempt(s): {last_error}"
    )
