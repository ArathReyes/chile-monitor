"""Email reports through AgentMail (REST, https://docs.agentmail.to).

Secrets/config (from .env locally, GitHub Actions secrets in the cloud):
    AGENTMAIL_API_KEY   API key (Bearer token). Never logged.
    AGENTMAIL_INBOX     sending inbox, e.g. name@agentmail.to (the inbox id)
    REPORT_TO           recipient address(es), comma-separated
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from monitor.config import get_secret

log = logging.getLogger(__name__)
API = "https://api.agentmail.to/v0"
MAX_BYTES = 5_000_000  # AgentMail caps the whole request at 6 MB


@dataclass
class Attachment:
    filename: str
    content: bytes
    content_type: str = "text/plain"

    def payload(self) -> dict[str, str]:
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "content_disposition": "attachment",
            "content": base64.b64encode(self.content).decode(),
        }


def build_message(
    to: list[str], subject: str, text: str, html: str | None, attachments: list[Attachment]
) -> dict:
    body: dict = {"to": to, "subject": subject, "text": text}
    if html:
        body["html"] = html
    kept, size = [], len(text) + len(html or "")
    for a in attachments:
        size += len(a.content) * 4 // 3
        if size > MAX_BYTES:
            log.warning("attachment %s dropped (request size limit)", a.filename)
            continue
        kept.append(a.payload())
    if kept:
        body["attachments"] = kept
    return body


def send_email(
    subject: str,
    text: str,
    html: str | None = None,
    attachments: list[Attachment] | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Send; returns AgentMail's message_id. Raises if unconfigured or the API refuses."""
    key, inbox, to = (get_secret(n) for n in ("AGENTMAIL_API_KEY", "AGENTMAIL_INBOX", "REPORT_TO"))
    if not (key and inbox and to):
        raise RuntimeError(
            "email not configured: set AGENTMAIL_API_KEY, AGENTMAIL_INBOX, REPORT_TO"
        )
    recipients = [t.strip() for t in to.split(",") if t.strip()]
    body = build_message(recipients, subject, text, html, attachments or [])
    url = f"{API}/inboxes/{quote(inbox, safe='')}/messages/send"
    c = client or httpx.Client(timeout=60)
    try:
        r = c.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    finally:
        if client is None:
            c.close()
    if r.status_code >= 400:
        raise RuntimeError(f"AgentMail {r.status_code}: {r.text[:300]}")
    return r.json().get("message_id", "")
