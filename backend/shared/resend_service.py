"""Resend-verzending (transactionele mail: rapporten, herinneringen, dagafsluitingen,
en straks klant-app-mail). Zelfde contract als email_service.send_report(), zodat
email_service dit als voorkeursprovider kan gebruiken zonder dat bellers het weten.

Bewust geen resend-SDK-dependency: httpx zit al in requirements.txt en de REST-API
is klein genoeg (één POST) om niet nóg een package binnen te halen.
"""
import logging

import httpx

from .config import RESEND_API_KEY, RESEND_FROM_EMAIL, REPORT_EMAIL_TO
from .markdown_email import to_html, strip_header

log = logging.getLogger(__name__)

_API_URL = "https://api.resend.com/emails"
_TIMEOUT = 15.0


def is_configured() -> bool:
    return bool(RESEND_API_KEY and RESEND_FROM_EMAIL)


def send_html(subject: str, html: str, to: str = None, text: str = None,
              from_email: str = None) -> bool:
    """Verstuur kant-en-klare HTML (voor toekomstige klant-templates)."""
    if not is_configured():
        return False

    recipient = to or REPORT_EMAIL_TO
    payload = {
        "from": from_email or RESEND_FROM_EMAIL,
        "to": [recipient],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text

    try:
        resp = httpx.post(
            _API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 400:
            log.warning("[Resend] Versturen mislukt (%s): %s",
                        resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as e:
        log.warning("[Resend] Versturen mislukt: %s", e)
        return False


def send_report(subject: str, body: str, to: str = None) -> bool:
    """Markdown-body -> HTML, zelfde contract als email_service.send_report()."""
    html = to_html(strip_header(body))
    return send_html(subject, html, to=to, text=body)
