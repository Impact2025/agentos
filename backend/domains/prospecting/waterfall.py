"""
Waterfall-verrijking — de "enrichment" stap uit de Marketing Agents masterclass.

De video (Greg Isenberg / Cody Schneider) draait een 'waterfall': goedkope/
nauwkeurige provider eerst, daarna duurdere fallbacks tot de data gevonden is.
Wij volgen exact dat patroon, maar dan key-gated en GDPR-safe (geen enkele
provider wordt aangeroepen zonder geldige API-key — een ontbrekende key slaat
de stap simpelweg over in plaats van te crashen).

E-mail-waterfall:   Hunter.io  →  GetLeads.io  →  Apollo
Telefoon:           Lead Magic  (aparte stap, na de e-mail-keten)

Elke provider retourneert een genormaliseerde lijst contacten:
    [{name, email, phone, title, company, source, confidence}]
zodat de orchestrator ze kan samenvoegen en dedupliceren op (email of naam).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ...shared.config import (
    HUNTER_API_KEY, GETLEADS_API_KEY, APOLLO_API_KEY, LEAD_MAGIC_API_KEY,
)
from ...shared.database import get_conn

log = logging.getLogger(__name__)

_TIMEOUT = 25.0


# ── Provider: Hunter.io (primaire e-mailverrijking) ──────────────────────────

def _hunter_domain_search(domain: str) -> List[Dict]:
    """Hunter.io — primaire e-mailverrijking voor een domein.

    Gedeeld met prospecting/hunter.py (zelfde API, zelfde key). We roepen het
    hier expliciet aan zodat run_waterfall ZELFSTANDIG correct is: hij wordt
    zowel vanuit enrich_lead() (waar Hunter al liep) als standalone via het
    /waterfall-endpoint aangeroepen. Zonder HUNTER_API_KEY → leeg (key-gated).
    """
    if not HUNTER_API_KEY or not domain:
        return []
    try:
        from .hunter import HunterService
        svc = HunterService()
        if not svc.is_configured():
            return []
        contacts = svc.domain_search(domain)
        # HunterService.domain_search geeft al genormaliseerde dicts terug
        # (first_name, last_name, email, position, confidence, email_status).
        out = []
        for c in contacts:
            email = (c.get("email") or "").strip().lower()
            if not email:
                continue
            out.append({
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                "email": email,
                "phone": "",
                "title": c.get("position", "") or "",
                "company": domain,
                "source": "hunter",
                "confidence": int(c.get("confidence") or 0),
            })
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("[waterfall] Hunter fout: %s", e)
        return []


# ── Provider: GetLeads.io ────────────────────────────────────────────────────

def _getleads_domain_search(domain: str) -> List[Dict]:
    """GetLeads.io — goedkopere/nauwkeurigere e-mailverrijking dan Hunter.

    API: POST https://api.getleads.io/api/enrich_domains  (of /v1/...) met
    {domains:[...], api_key}. Antwoord vorm wisselt per plan; we verwerken
    zowel 'people' als 'contacts' als 'emails' substructuren defensief.
    """
    if not GETLEADS_API_KEY or not domain:
        return []
    try:
        resp = httpx.post(
            "https://api.getleads.io/api/enrich_domains",
            headers={"Authorization": f"Bearer {GETLEADS_API_KEY}",
                      "Content-Type": "application/json"},
            json={"domains": [domain], "limit": 10},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403, 429):
            log.warning("[waterfall] GetLeads HTTP %s — overslaan", resp.status_code)
            return []
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("[waterfall] GetLeads fout: %s", e)
        return []

    out: List[Dict] = []
    # GetLeads wrappt resultaten per domein onder 'results' of geeft een platte
    # lijst mensen terug — vang beide.
    people = (data.get("results") or {}).get(domain) or data.get("people") \
        or data.get("contacts") or data.get("emails") or []
    if isinstance(people, dict):
        people = people.get("people", [])
    for p in people or []:
        email = (p.get("email") or "").strip().lower()
        if not email:
            continue
        out.append({
            "name": (p.get("first_name") or "") + " " + (p.get("last_name") or ""),
            "email": email,
            "phone": p.get("phone", "") or "",
            "title": p.get("title") or p.get("position") or p.get("role") or "",
            "company": p.get("company") or p.get("organization") or "",
            "source": "getleads",
            "confidence": int(p.get("confidence") or 50),
        })
    return out


# ── Provider: Apollo.io ──────────────────────────────────────────────────────

def _apollo_domain_search(domain: str) -> List[Dict]:
    """Apollo.io — brede B2B-contactverrijking (fallback na GetLeads).

    API: POST https://api.apollo.io/api/v1/mixed_people/search met
    {api_key, q: domain, page:1, per_page:10}. Apollo retourneert 'people'
    met email/phone/title.
    """
    if not APOLLO_API_KEY or not domain:
        return []
    try:
        resp = httpx.post(
            "https://api.apollo.io/api/v1/mixed_people/search",
            headers={"Content-Type": "application/json",
                      "X-Api-Key": APOLLO_API_KEY},
            json={"q": domain, "page": 1, "per_page": 10,
                  "organization_domains": [domain]},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403, 429):
            log.warning("[waterfall] Apollo HTTP %s — overslaan", resp.status_code)
            return []
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("[waterfall] Apollo fout: %s", e)
        return []

    out: List[Dict] = []
    for p in data.get("people", []) or []:
        email = (p.get("email") or "").strip().lower()
        if not email:
            continue
        out.append({
            "name": (p.get("first_name") or "") + " " + (p.get("last_name") or ""),
            "email": email,
            "phone": p.get("phone_number") or p.get("phone") or "",
            "title": p.get("title") or p.get("headline") or "",
            "company": (p.get("organization") or {}).get("name", "")
                       if isinstance(p.get("organization"), dict) else "",
            "source": "apollo",
            "confidence": int(p.get("confidence_score") or 50),
        })
    return out


# ── Provider: Lead Magic (telefoon) ──────────────────────────────────────────

def _leadmagic_phone(domain: str) -> List[Dict]:
    """Lead Magic — telefoonnummers voor een domein (aparte stap na e-mail).

    API: GET/POST https://api.leadmagic.net/v1/... met api_key. De exacte
    endpoint-vorm varieert per abonnement; we proberen de meest gebruikte
    'domain_phones' lookup en vangen alles defensief af. Geen key → leeg.
    """
    if not LEAD_MAGIC_API_KEY or not domain:
        return []
    try:
        resp = httpx.post(
            "https://api.leadmagic.net/v1/domain/phones",
            headers={"Authorization": f"Bearer {LEAD_MAGIC_API_KEY}",
                      "Content-Type": "application/json"},
            json={"domain": domain},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403, 429):
            log.warning("[waterfall] Lead Magic HTTP %s — overslaan", resp.status_code)
            return []
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("[waterfall] Lead Magic fout: %s", e)
        return []

    out: List[Dict] = []
    phones = data.get("phones") or data.get("data") or []
    if isinstance(phones, dict):
        phones = phones.get("phones", [])
    for ph in phones or []:
        num = (ph.get("phone") if isinstance(ph, dict) else str(ph)).strip()
        if not num:
            continue
        out.append({
            "name": ph.get("name", "") if isinstance(ph, dict) else "",
            "email": "",
            "phone": num,
            "title": ph.get("title", "") if isinstance(ph, dict) else "",
            "company": domain,
            "source": "leadmagic",
            "confidence": int(ph.get("confidence", 50)) if isinstance(ph, dict) else 50,
        })
    return out


# ── Orchestrator ─────────────────────────────────────────────────────────────

def _dedupe(contacts: List[Dict]) -> List[Dict]:
    """Samenvoegen op e-mail; zonder e-mail op (naam+telefoon)."""
    by_email: Dict[str, Dict] = {}
    by_phone: Dict[str, Dict] = {}
    out: List[Dict] = []
    for c in contacts:
        email = (c.get("email") or "").lower()
        phone = (c.get("phone") or "").strip()
        if email:
            if email in by_email:
                # behoud de rij met de meeste velden
                if len(c) > len(by_email[email]):
                    by_email[email] = c
                continue
            by_email[email] = c
            out.append(c)
        elif phone:
            key = f"p:{phone}"
            if key in by_phone:
                continue
            by_phone[key] = c
            out.append(c)
        else:
            out.append(c)
    return out


def run_waterfall(lead: Dict, *, include_phone: bool = True) -> Dict[str, Any]:
    """Verrijk een lead via de volledige waterfall-keten.

    Args:
        lead: de lead-dict (moet 'website', 'email', 'contacts', 'phone' hebben).
        include_phone: ook Lead Magic (telefoon) proberen.

    Returns een rapport:
        {providers_tried, email_found, phone_found, added_contacts,
         primary_email, primary_phone, sources_used}
    Bij geen enkele key geconfigureerd: rapport met lege resultaten (geen fout).
    """
    domain = (lead.get("website") or "").lower()
    # kaal domein
    if domain.startswith("http"):
        from urllib.parse import urlparse
        domain = urlparse(domain).netloc
    domain = domain[4:] if domain.startswith("www.") else domain
    if not domain:
        return {"providers_tried": [], "email_found": False, "phone_found": False,
                "added_contacts": [], "primary_email": lead.get("email", ""),
                "primary_phone": lead.get("phone", ""), "sources_used": []}

    existing = lead.get("contacts") or []
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except Exception:
            existing = []
    existing_emails = {c.get("email", "").lower() for c in existing if c.get("email")}

    merged: List[Dict] = list(existing)
    tried: List[str] = []
    sources_used: List[str] = []

    # ── E-mail-waterfall ──
    # Primair Hunter.io, daarna de goedkopere/nauwkeurigere fallbacks
    # (GetLeads → Apollo) als Hunter geen e-mail oplevert. Elke provider is
    # key-gated: zonder key slaat die provider over. De keten stopt zodra een
    # provider een e-mail vindt (goedkoopste eerst).
    primary_email = (lead.get("email") or "").strip().lower()

    if not primary_email and HUNTER_API_KEY:
        tried.append("hunter")
        for c in _hunter_domain_search(domain):
            if c["email"] not in existing_emails:
                merged.append(c)
                existing_emails.add(c["email"])
                sources_used.append("hunter")
                if not primary_email:
                    primary_email = c["email"]

    if not primary_email and GETLEADS_API_KEY:
        tried.append("getleads")
        for c in _getleads_domain_search(domain):
            if c["email"] not in existing_emails:
                merged.append(c)
                existing_emails.add(c["email"])
                sources_used.append("getleads")
                if not primary_email:
                    primary_email = c["email"]

    if not primary_email and APOLLO_API_KEY:
        tried.append("apollo")
        for c in _apollo_domain_search(domain):
            if c["email"] not in existing_emails:
                merged.append(c)
                existing_emails.add(c["email"])
                sources_used.append("apollo")
                if not primary_email:
                    primary_email = c["email"]

    # ── Telefoon (Lead Magic) ──
    primary_phone = (lead.get("phone") or "").strip()
    if include_phone and not primary_phone and LEAD_MAGIC_API_KEY:
        tried.append("leadmagic")
        for c in _leadmagic_phone(domain):
            # Alleen telefoon toevoegen als die er niet al in zit.
            has_phone = any(c.get("phone") == c2.get("phone")
                            for c2 in merged if c2.get("phone"))
            if not has_phone:
                merged.append(c)
                sources_used.append("leadmagic")
                if not primary_phone:
                    primary_phone = c["phone"]

    merged = _dedupe(merged)
    return {
        "providers_tried": tried,
        "email_found": bool(primary_email),
        "phone_found": bool(primary_phone),
        "added_contacts": [c for c in merged if c not in existing],
        "primary_email": primary_email,
        "primary_phone": primary_phone,
        "sources_used": sorted(set(sources_used)),
        "total_contacts": len(merged),
    }


def waterfall_report() -> Dict[str, bool]:
    """Welke providers zijn geconfigureerd (voor de UI/status)."""
    return {
        "hunter": bool(HUNTER_API_KEY),
        "getleads": bool(GETLEADS_API_KEY),
        "apollo": bool(APOLLO_API_KEY),
        "leadmagic": bool(LEAD_MAGIC_API_KEY),
    }
