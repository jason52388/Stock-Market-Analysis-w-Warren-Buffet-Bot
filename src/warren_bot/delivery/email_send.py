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

    password = os.environ["GMAIL_APP_PASSWORD"]
    with smtplib.SMTP(email_cfg["smtp_host"], int(email_cfg["smtp_port"])) as smtp:
        smtp.starttls()
        smtp.login(email_cfg["from_addr"], password)
        smtp.send_message(msg)
