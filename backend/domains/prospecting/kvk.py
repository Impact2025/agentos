"""
Gratis KvK-lookup via OpenKVK.nl community API.
Valt stilletjes terug (None) als de service onbereikbaar is of geen resultaat geeft.
Geen API-sleutel vereist.
"""
import logging
from typing import Optional, Dict

import httpx

log = logging.getLogger(__name__)

_API_URL = "https://api.openkvk.nl/json/"


def lookup_by_name(company_name: str, city: str = "") -> Optional[Dict]:
    """
    Zoek een bedrijf op naam (+ optioneel gemeente) via OpenKVK.nl.
    Geeft dict terug met kvk_number, address, postal_code, city — of None bij mislukking.
    """
    if not company_name:
        return None

    params: Dict = {"BEDRIJFSNAAM": company_name[:80]}
    if city:
        params["GEMEENTE"] = city[:50]

    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(
                _API_URL,
                params=params,
                headers={"User-Agent": "ImpactOS/1.0 lead-research"},
            )
            if r.status_code != 200:
                return None

            data = r.json()
            results = (data.get("data") or {}).get("results") or []
            if not results:
                return None

            hit = results[0]
            kvk = str(hit.get("KVKNUMMER") or "").strip()
            if not kvk:
                return None

            straat = str(hit.get("STRAAT") or "").strip()
            huisnr = str(hit.get("HUISNUMMER") or "").strip()
            adres = f"{straat} {huisnr}".strip()

            return {
                "kvk_number": kvk,
                "address": adres,
                "postal_code": str(hit.get("POSTCODE") or "").strip().upper(),
                "city": str(hit.get("GEMEENTE") or "").strip().title(),
            }

    except Exception as e:
        log.debug("[kvk] OpenKVK lookup mislukt voor '%s': %s", company_name, e)
        return None
