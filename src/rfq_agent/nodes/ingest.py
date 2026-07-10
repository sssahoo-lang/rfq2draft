"""Ingest node: PDF / .eml ? RFQDocument."""

from __future__ import annotations

import email
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

from pypdf import PdfReader

from rfq_agent.config import MIN_PDF_TEXT_CHARS, RUNS_DIR
from rfq_agent.schemas import RFQDocument, SourceType


def _parse_eml(path: Path) -> tuple[str | None, str | None, str | None, str]:
    with path.open("rb") as handle:
        msg = email.message_from_binary_file(handle, policy=policy.default)

    sender = msg.get("From")
    subject = msg.get("Subject")
    sent_date: str | None = None
    date_header = msg.get("Date")
    if date_header:
        try:
            sent_date = parsedate_to_datetime(date_header).date().isoformat()
        except (TypeError, ValueError, IndexError):
            sent_date = None

    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        raw_text = msg.get_content() if msg.is_multipart() is False else ""
        if not isinstance(raw_text, str):
            raw_text = str(raw_text)
    else:
        content = body.get_content()
        raw_text = content if isinstance(content, str) else str(content)

    return sender, subject, sent_date, raw_text.strip()


def _parse_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
    return "\n".join(parts).strip()


def ingest(source_path: str | Path) -> RFQDocument:
    """Load a PDF or .eml RFQ into an RFQDocument and write runs/<id>/document.json."""
    path = Path(source_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"RFQ source not found: {path}")

    suffix = path.suffix.lower()
    run_id = path.stem
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if suffix == ".eml":
        sender, subject, sent_date, raw_text = _parse_eml(path)
        source_type = SourceType.eml
    elif suffix == ".pdf":
        raw_text = _parse_pdf(path)
        # TODO(P4): multimodal Claude fallback when text extraction is too thin
        if len(raw_text) < MIN_PDF_TEXT_CHARS:
            raise RuntimeError(
                "PDF text extraction produced too little text; multimodal "
                "fallback arrives with the LLM step"
            )
        sender = None
        subject = None
        sent_date = None
        source_type = SourceType.pdf
    else:
        raise ValueError(f"Unsupported RFQ source type: {suffix}")

    document = RFQDocument(
        run_id=run_id,
        source_path=str(path),
        source_type=source_type,
        sender=sender,
        subject=subject,
        sent_date=sent_date,
        raw_text=raw_text,
    )
    (run_dir / "document.json").write_text(
        document.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return document
