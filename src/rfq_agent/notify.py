"""Optional real email send via Gmail SMTP -- OPT-IN, OFF BY DEFAULT.

The default pipeline mocks the send (see nodes/mocks.py: sent_email.eml). This
module is only used when the reviewer explicitly chooses to send for real, and
it only ever sends an APPROVED quote.

Setup (done by the user, never by the tool):
  1. Enable 2-Step Verification on the Gmail account.
  2. Create an App Password (Google Account -> Security -> App passwords).
  3. Put these in .env:
        GMAIL_ADDRESS=you@gmail.com
        GMAIL_APP_PASSWORD=the-16-char-app-password
No credential ever lives in the code or the repo; both are read from the
environment at send time. If either is unset, sending is disabled and the
mock remains the only behavior.

Note: the sample RFQ recipient domains are fictional and will bounce, so a
real demo send should target your own address via to_override.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from rfq_agent.config import RUNS_DIR
from rfq_agent.quote_pdf import render_quote_pdf_bytes
from rfq_agent.schemas import PackageStatus, QuotePackage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class EmailSendError(Exception):
    """Human-readable failure for the optional real-send path."""


def gmail_configured() -> bool:
    """True only if the user has supplied both Gmail env vars."""
    return bool(os.environ.get("GMAIL_ADDRESS")) and bool(
        os.environ.get("GMAIL_APP_PASSWORD")
    )


def _build_message(package: QuotePackage, sender: str, recipient: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"Southeast Hose & Fitting Co. <{sender}>"
    msg["To"] = recipient
    msg["Subject"] = package.email_subject or f"Quotation {package.quote_id}"
    msg.set_content(package.email_body or "")
    msg.add_attachment(
        render_quote_pdf_bytes(package),
        maintype="application",
        subtype="pdf",
        filename=f"Quotation-{package.quote_id}.pdf",
    )
    return msg


def send_via_gmail(
    package: QuotePackage,
    *,
    to_override: str | None = None,
    force: bool = False,
) -> dict:
    """Send an approved quote for real via Gmail SMTP. Gated + idempotent.

    Raises EmailSendError with a clear message on any precondition failure.
    """
    # Gate 1: only approved quotes are ever sent.
    if package.status != PackageStatus.approved:
        raise EmailSendError(
            "Refusing to send: this quote is not approved. Approve it first."
        )

    # Gate 2: must be explicitly configured.
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise EmailSendError(
            "Real Gmail send is not configured. Set GMAIL_ADDRESS and "
            "GMAIL_APP_PASSWORD (a Gmail App Password) in your .env to enable "
            "it. Without them, the mock (sent_email.eml) is the only behavior."
        )

    recipient = to_override or package.email_to
    if not recipient:
        raise EmailSendError("No recipient address available for this quote.")

    # Gate 3: idempotency -- do not double-send unless forced.
    marker = RUNS_DIR / package.run_id / "sent_via_gmail.json"
    if marker.exists() and not force:
        prior = json.loads(marker.read_text(encoding="utf-8"))
        raise EmailSendError(
            f"Already sent for {package.quote_id} to {prior.get('sent_to')} "
            f"at {prior.get('sent_at')}. Use force to resend."
        )

    msg = _build_message(package, address, recipient)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
            smtp.login(address, app_password)
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001 -- surface any SMTP/auth error cleanly
        raise EmailSendError(f"Gmail send failed: {exc}") from exc

    record = {
        "quote_id": package.quote_id,
        "sent_to": recipient,
        "sent_from": address,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
