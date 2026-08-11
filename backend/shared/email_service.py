"""
E-mailverzending voor rapporten (financieel, GA, Iris-briefing, digest).
send_report() is het chokepoint dat alle bellers gebruiken: probeert Resend
eerst (betere afleverbaarheid, geen SMTP-relay nodig), valt terug op SMTP
zodra Resend niet geconfigureerd is of faalt. Bellers hoeven niets te weten
van welke provider daadwerkelijk verstuurt.
Ondersteunt STARTTLS (poort 587) en SSL (poort 465) voor de SMTP-tak.
Stuurt altijd zowel plain-text als HTML (markdown gerenderd).
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_EMAIL_TO
from .markdown_email import to_html, strip_header

log = logging.getLogger(__name__)


def is_configured() -> bool:
    """Minstens één verzendkanaal (Resend of SMTP) bruikbaar."""
    from . import resend_service
    return resend_service.is_configured() or _smtp_configured()


def _smtp_configured() -> bool:
    return all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD])


def send_report(subject: str, body: str, to: str = None) -> bool:
    """Chokepoint voor alle rapport-mail. Resend eerst, SMTP als terugval.

    Bellers (digest, finance/GA-rapporten, Iris-briefing, agenda-herinnering)
    blijven ongewijzigd — zij roepen alleen deze functie aan.
    """
    from . import resend_service
    if resend_service.is_configured():
        if resend_service.send_report(subject, body, to=to):
            return True
        log.warning("[Email] Resend mislukt, val terug op SMTP")

    return _send_via_smtp(subject, body, to=to)


def _send_via_smtp(subject: str, body: str, to: str = None) -> bool:
    if not _smtp_configured():
        return False

    recipient = to or REPORT_EMAIL_TO
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = recipient

    # Plain text als fallback, HTML als voorkeur
    plain = body
    html = to_html(strip_header(body))

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, recipient, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, recipient, msg.as_string())
        return True
    except Exception as e:
        log.warning("[Email] SMTP-verzending mislukt: %s", e)
        return False
