"""SMTP email send via Gmail app password.

Why not the user's Gmail MCP? MCP runs in the interactive session, not in unattended
GH Actions cron. An app password is the standard headless path.
Generate one at https://myaccount.google.com/apppasswords and set GMAIL_APP_PASSWORD.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

_SMTP_TIMEOUT_SECONDS = 30


def _is_retryable(exc: BaseException) -> bool:
    """Retry only true connection-level failures.

    NB: `SMTPAuthenticationError` inherits from `OSError` via the SMTP exception
    chain, so we can't just list `OSError` in the retry set — that would retry
    bad-password failures three times before giving up, which is both pointless
    and an audit-log smell on the Gmail side. Filter SMTP response errors out
    explicitly: those represent a server-side rejection that retrying can't fix.
    """
    if isinstance(exc, smtplib.SMTPResponseException):
        return False
    return isinstance(exc, (
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPConnectError,
        TimeoutError,
        ConnectionError,
    ))


def send_email(subject: str, html: str, email_cfg: dict,
               attachments: list[Path] | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = email_cfg["from_addr"]
    msg["To"] = email_cfg["to_addr"]
    msg["Subject"] = subject
    msg.set_content("This email requires an HTML-capable client.")
    msg.add_alternative(html, subtype="html")

    for path in attachments or []:
        data = path.read_bytes()
        # HTML attachments need MIME type text/html so most mail clients render
        # them as previewable instead of forcing a download.
        if path.suffix.lower() == ".html":
            msg.add_attachment(data, maintype="text", subtype="html", filename=path.name)
        else:
            msg.add_attachment(data, maintype="application", subtype="octet-stream",
                               filename=path.name)

    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD env var is not set — cannot authenticate to Gmail SMTP."
        )
    _do_send(msg, email_cfg, password)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _do_send(msg: EmailMessage, email_cfg: dict, password: str) -> None:
    with smtplib.SMTP(
        email_cfg["smtp_host"],
        int(email_cfg["smtp_port"]),
        timeout=_SMTP_TIMEOUT_SECONDS,
    ) as smtp:
        smtp.starttls()
        smtp.login(email_cfg["from_addr"], password)
        smtp.send_message(msg)
