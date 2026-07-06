"""
Hunter.io API — domein-zoekopdracht en e-mailverificatie.

Gratis plan: 25 zoekopdrachten + 50 verificaties per maand.
API docs: https://hunter.io/api-documentation/v2

Gebruik:
  domain_search(domain_or_url) → contacten voor dat domein
  email_verify(email)          → deliverability-check
  enrich_contacts(contacts)    → verifieert e-mails in bestaande lijst
"""
import logging
from typing import List, Dict
from urllib.parse import urlparse

import httpx

from ...shared.config import HUNTER_API_KEY

log = logging.getLogger(__name__)
_BASE = "https://api.hunter.io/v2"


def _extract_domain(url_or_domain: str) -> str:
    s = url_or_domain.strip()
    if not s:
        return ""
    if "://" not in s and not s.startswith("www."):
        return s.lower()
    try:
        parsed = urlparse(s if "://" in s else f"https://{s}")
        d = parsed.netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return s.lower()


class HunterService:
    def __init__(self):
        self._ok = bool(HUNTER_API_KEY)

    def is_configured(self) -> bool:
        return self._ok

    def domain_search(self, domain_or_url: str, max_emails: int = 10) -> List[Dict]:
        """
        Zoekt e-mailcontacten voor een domein.
        Retourneert [{first_name, last_name, email, position, confidence, email_status}].
        Leeg bij geen HUNTER_API_KEY, rate-limit, of fout.
        """
        if not self._ok:
            return []
        domain = _extract_domain(domain_or_url)
        if not domain:
            return []
        try:
            with httpx.Client(timeout=20.0) as c:
                r = c.get(
                    f"{_BASE}/domain-search",
                    params={
                        "domain":  domain,
                        "api_key": HUNTER_API_KEY,
                        "limit":   max_emails,
                    },
                )
                if r.status_code == 429:
                    log.warning("[hunter] Rate limit — domein %s overgeslagen", domain)
                    return []
                if r.status_code in (401, 403):
                    log.error("[hunter] Ongeldige API-sleutel (HTTP %s)", r.status_code)
                    return []
                r.raise_for_status()
                emails = r.json().get("data", {}).get("emails", [])
                return [
                    {
                        "first_name":   e.get("first_name") or "",
                        "last_name":    e.get("last_name") or "",
                        "email":        e.get("value") or "",
                        "position":     e.get("position") or "",
                        "confidence":   int(e.get("confidence") or 0),
                        "email_status": "",   # wordt ingevuld door verify_contacts()
                        "email_score":  0,
                    }
                    for e in emails
                    if e.get("value")
                ]
        except Exception as ex:
            log.error("[hunter] domain_search fout: %s", ex)
            return []

    def email_verify(self, email: str) -> Dict:
        """
        Verifieert één e-mailadres.
        Retourneert {result: deliverable|undeliverable|risky|unknown, score: int}.
        """
        if not self._ok or not email:
            return {"result": "unknown", "score": 0}
        try:
            with httpx.Client(timeout=25.0) as c:
                r = c.get(
                    f"{_BASE}/email-verifier",
                    params={"email": email, "api_key": HUNTER_API_KEY},
                )
                if r.status_code in (401, 403, 429):
                    log.warning("[hunter] verify %s — HTTP %s", email, r.status_code)
                    return {"result": "unknown", "score": 0}
                r.raise_for_status()
                data = r.json().get("data", {})
                return {
                    "result": data.get("result") or "unknown",
                    "score":  int(data.get("score") or 0),
                }
        except Exception as ex:
            log.error("[hunter] email_verify fout: %s", ex)
            return {"result": "unknown", "score": 0}

    def verify_contacts(self, contacts: List[Dict]) -> List[Dict]:
        """
        Verifieert e-mails van een contacten-lijst (hoogste confidence eerst).
        Voegt `email_status` en `email_score` toe aan elk contact.
        Retourneert de gesorteerde, verrijkte lijst.
        """
        if not self._ok:
            return contacts
        enriched = []
        for c in sorted(contacts, key=lambda x: int(x.get("confidence") or 0), reverse=True):
            email = c.get("email", "")
            if email and not c.get("email_status"):
                v = self.email_verify(email)
                c = {**c, "email_status": v["result"], "email_score": v["score"]}
            enriched.append(c)
        return enriched

    def first_deliverable(self, contacts: List[Dict]) -> Dict:
        """Geeft het eerste contact terug met een deliverable e-mail, of {}."""
        for c in contacts:
            if c.get("email_status") == "deliverable":
                return c
        return {}
