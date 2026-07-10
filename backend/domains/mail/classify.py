"""Classificeer een inkomende mail naar type, zodat we alleen echte vragen
naar een concept-antwoord sturen."""
import re

NEWSLETTER_HINTS = (
    "unsubscribe", "newsletter", "web push", "built for developers",
    "view in browser", "five ways to", "getting started",
)
INVOICE_HINTS = ("factuur", "invoice", "btw", "betaal", "aanmaning")
QUESTION_HINTS = (
    "?", "hoe", "wat", "kan", "kunt", "help", "vraag", "probleem",
    "werkt niet", "niet meer", "reset", "wachtwoord", "inloggen", "account",
    "bestelling", "levering", "error", "fout",
)


def classify(subject: str, body: str) -> str:
    s = (subject or "").lower()
    b = (body or "").lower()
    if any(h in s or h in b for h in NEWSLETTER_HINTS):
        return "newsletter"
    if any(h in s or h in b for h in INVOICE_HINTS):
        # factuur: niet automatisch beantwoorden, maar wel zichtbaar laten
        return "invoice"
    if any(h in s or h in b for h in QUESTION_HINTS) or "?" in b:
        return "question"
    return "other"
