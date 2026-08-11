"""Canonicaliseer de 7 resterende echte dubbele-pagina-bevindingen met een 301.

Veiligheidsmodel:
- DRY-RUN default: doet ALLEEN ontdekking + reporting, geen enkele write naar een site.
- --execute alleen na expliciete bevestiging dat (a) de per-site publish-API een
  redirect/alias/canonical veld ondersteunt en (b) de LLM-quota niet meer op 403 zit.
- BVJ (Railway) wordt altijd overgeslagen: die backend kent alleen POST-publish,
  geen redirect. Escalatie via backend-toegang / canonical-tag / CMS.

De 7 paren (geverifieerd live op 2026-08-10, beide kanten 200):
  BVJ        levensverhaal-vastleggen-complete-gids-voor-2026 <-> complete-gids-levensverhaal-vastleggen
  Steentjebij 4-microgewoontes-om-je-relatie-te-verdiepen <-> ...-2
  Steentjebij 7-manieren-om-speelsheid-in-je-relatie-te-brengen-* (2 varianten)
  Steentjebij ritual-box-voor-stellen-recensie-onze-ervaring-met-* (2 varianten)
  Steentjebij jubileum-cadeau-ideeen-die-echt-verbinden-5-blijve-* (2 varianten)
  Steentjebij oxytocine-relatiespel-voor-koppels-7-manieren-waar-* (2 varianten)
  Pootgelukkig wat-kost-een-huisdier-maand-jaarkosten <-> ...-jx9k

Kanonieke keuze (deterministisch, geen LLM nodig):
  - slug zonder '-2' / '-N' achtervoegsel wint
  - slug zonder vuile suffix (bv '-jx9k') wint
  - bij gelijke slugs: de URL die als eerste in de sitemap staat (hier: de
    eerst-getoonde in de bevinding), tenzij analytics anders aangeeft.

Gebruik:
  python scripts/canonicalize_duplicates.py            # dry-run, alleen report
  python scripts/canonicalize_duplicates.py --execute # echte 301 (na API-check!)
"""
from __future__ import annotations
import argparse
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO, "data", "agentos.db")
ENV = os.path.join(REPO, ".env")


def load_env() -> dict:
    out = {}
    if not os.path.exists(ENV):
        return out
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# (site_env_prefix, base_url, canonical_slug, redirect_slug)
# redirect_slug wordt 301'd naar canonical_slug.
PAREN = [
    ("STEENTJEBIJSTEENTJE", "https://www.steentjebijsteentje.nl",
     "4-microgewoontes-om-je-relatie-te-verdiepen",
     "4-microgewoontes-om-je-relatie-te-verdiepen-2"),
    ("STEENTJEBIJSTEENTJE", "https://www.steentjebijsteentje.nl",
     "7-manieren-om-speelsheid-in-je-relatie-te-brengen",
     None),  # beide slugs afgekapt in audit; zie opmerking hieronder
    ("STEENTJEBIJSTEENTJE", "https://www.steentjebijsteentje.nl",
     "ritual-box-voor-stellen-recensie-onze-ervaring-met", None),
    ("STEENTJEBIJSTEENTJE", "https://www.steentjebijsteentje.nl",
     "jubileum-cadeau-ideeen-die-echt-verbinden-5-blijve", None),
    ("STEENTJEBIJSTEENTJE", "https://www.steentjebijsteentje.nl",
     "oxytocine-relatiespel-voor-koppels-7-manieren-waar", None),
    ("POOTGELUKKIG", "https://www.pootgelukkig.nl",
     "wat-kost-een-huisdier-maand-jaarkosten",
     "wat-kost-een-huisdier-maand-jaarkosten-jx9k"),
    # BVJ: altijd overslaan (geen API-redirect). Explicit hier voor transparantie.
    ("BEWAARDVOORJOU", "https://bewaardvoorjou.nl",
     "levensverhaal-vastleggen-complete-gids-voor-2026",
     "complete-gids-levensverhaal-vastleggen", "SKIP_NO_API"),
]


def _site_prefixes(env: dict) -> dict:
    """Map site-env-prefix -> (publish_url, publish_key)."""
    out = {}
    for k, v in env.items():
        if k.endswith("_PUBLISH_URL"):
            prefix = k[:-len("_PUBLISH_URL")]
            key = env.get(prefix + "_PUBLISH_KEY", "")
            out[prefix] = (v.strip(), key)
    return out


def _discover_redirect_field(prefix: str, pub_url: str, key: str) -> str | None:
    """Probeer via OPTIONS/GET of de API een redirect-veld documenteert.

    Geen writes: alleen kijken of het endpoint bestaat en welke velden het
    accepteert. Geeft een aanwijzing, geen garantie. Bij twijfel: None.
    """
    import json as _json
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            pub_url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", "ignore") if e.fp else ""
    except Exception:
        return None
    # 401/403/404/405 = geen lees-toegang of geen redirect-ondersteuning bekend;
    # we kunnen niet afleiden welk veld. Return None = onbekend.
    if status in (401, 403, 404, 405, 422):
        return None
    # Als de API een JSON-schema teruggeeft, zoek naar redirect/alias/canonical
    try:
        txt = _json.dumps(_json.loads(raw)).lower()
        for f in ("redirect", "alias", "canonical", "redirect_from", "redirect_to"):
            if f in txt:
                return f
    except Exception:
        pass
    return None


def _mark_resolved(conn, canonical_slug: str, redirect_slug: str) -> int:
    """Markeer de bijbehorende integriteits-bevinding als resolved (bewijs-gedreven)."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE integrity_findings SET resolved_at = datetime('now') "
        "WHERE invariant = 'sitemap_dubbele_pagina' AND resolved_at IS NULL "
        "AND (detail LIKE ? OR detail LIKE ?)",
        (f"%/{redirect_slug[:40]}%", f"%/{canonical_slug[:40]}%"),
    )
    n = cur.rowcount
    conn.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="Voer de 301's echt uit (alleen na API-check!).")
    args = ap.parse_args()

    env = load_env()
    sites = _site_prefixes(env)
    conn = sqlite3.connect(DB)

    print(f"DRY-RUN" if not args.execute else "EXECUTE", "— canonicalisatie 7 paren\n")

    for entry in PAREN:
        prefix, base, canon, redir = entry[0], entry[1], entry[2], entry[3]
        skip = entry[4] if len(entry) > 4 else None
        pub_url, key = sites.get(prefix, ("", ""))

        print(f"[{prefix}] {canon}")
        if redir:
            print(f"   301: /{redir} -> /{canon}")
        else:
            print(f"   (afgekapte slugs in audit — vul canonical/redirect slug handmatig in)")
        print(f"   publish_url: {pub_url or '(niet gevonden)'}")

        if skip == "SKIP_NO_API":
            print("   -> OVERGESLAGEN: BVJ Railway kent geen redirect-API. Escalatie nodig.\n")
            # markeer niet als resolved; blijft open voor menselijke actie
            continue

        if not args.execute:
            field = _discover_redirect_field(prefix, pub_url, key) if pub_url else None
            print(f"   redirect-veld (probe): {field or 'onbekend — NIET uitvoeren zonder check'}\n")
            continue

        # --execute pad
        if not pub_url or not key:
            print("   -> GEEN publish-URL/key — overslaan.\n")
            continue
        field = _discover_redirect_field(prefix, pub_url, key)
        if not field:
            print("   -> GEEN redirect-veld ontdekt — WEIGER blinde write. Los handmatig op.\n")
            continue
        if redir is None:
            print("   -> afgekapte slug, canonical/redirect onbekend — overslaan.\n")
            continue
        # Hier pas de echte 301-POST (niet geïmplementeerd: vereist per-site
        # payload-vorm, die verschilt per backend). Placeholder:
        print(f"   -> ZOU 301 doen via veld '{field}' — implementatie per site nodig.\n")
        # _mark_resolved(conn, canon, redir)  # pas na echte 301

    conn.close()
    print("Klaar. BVJ-paar blijft open (geen API-redirect).")


if __name__ == "__main__":
    main()
