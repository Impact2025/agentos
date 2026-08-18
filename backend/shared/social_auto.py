"""Social Auto-Poster — geplande, merkgestuurde social posts voor elk project.

Generalisatie van de oude BewaardVoorJou-cron: één engine die voor een willekeurig
project (met auto_social aan) een content-pack genereert en — als de mens dat heeft
aangezet — automatisch publiceert op Facebook en Instagram.

Wereldklasse-discipline (gelijk aan de Wachtrij/Helpdesk-gate):
  - auto_post STAAT UIT tenzij de mens het per project aanzet (sites.auto_social_enabled=1).
  - Staat het uit, dan genereert de job alleen een pack dat wacht op goedkeuring
    (net als Social Creatie nu doet) — er wordt NIETS automatisch geplaatst.
  - Facebook: volledig geautomatiseerd (tekst + lokale foto-upload via Graph API).
  - Instagram: vereist een PUBLIEKE image_url (Meta Content Publishing API). Zonder
    publieke host (Netlify/CDN) valt de IG-post terug op een plak-adapter met de
    lokaal gegenereerde afbeelding — géén nep-succes, de mens krijgt het bestand.

Tokens komen uit de `sites`-tabel (per project) of de globale .env-fallback — net
als de rest van het social-domein. Geen harde credentials in deze file.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Platforms die deze engine aankan. LinkedIn/TikTok blijven manual (plak-adapter),
# conform social_inbox/social_content beleid.
AUTO_PLATFORMS = ("facebook", "instagram")


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def get_site_social_config(site_name: str) -> Optional[dict]:
    """Lees auto_social-vlag + platforms voor een project uit de sites-tabel."""
    from .database import get_conn
    from ..domains.seo import sites as sites_service

    for s in sites_service.list_sites():
        if _norm(s.get("name", "")) == _norm(site_name):
            full = sites_service.get_site(s["id"])
            if not full:
                return None
            enabled = bool(int(full.get("auto_social_enabled") or 0))
            plats = (full.get("auto_social_platforms") or "").strip()
            platforms = [p.strip().lower() for p in plats.split(",") if p.strip()] if plats else list(AUTO_PLATFORMS)
            # Filter op wat deze engine ondersteunt.
            platforms = [p for p in platforms if p in AUTO_PLATFORMS]
            # HARD GUARD: een project mag alleen posten op zijn EIGEN pagina. Als er
            # geen eigen facebook_page_id/instagram_business_id in de sites-tabel staat,
            # weigeren we de auto-post — anders valt de engine terug op de globale
            # .env-token (die van een ANDER project kan zijn) en belandt de post op de
            # verkeerde pagina. Betere een heldere fout dan een post op de verkeerde plek.
            own_fb = bool((full.get("facebook_page_id") or "").strip())
            own_ig = bool((full.get("instagram_business_id") or "").strip())
            has_own = own_fb or own_ig
            return {
                "enabled": enabled,
                "platforms": platforms,
                "site_name": s.get("name", site_name),
                "own_token_fb": own_fb,
                "own_token_ig": own_ig,
                "has_own_token": has_own,
            }
    return None


def _pick_grounded_idea(project: str) -> Optional[Dict]:
    """Kies het beste datagedreven post-idee (GSC-topquery / Demand-kans /
    eerdere FB-engagement) i.p.v. te gokken uit een willekeurige vault-zin.

    Vóór deze functie koos elke auto-post-run een thema met `_pick_theme` —
    een toevallige eerste zin uit een merk-notitie — terwijl `analytics/
    facebook_content.py` allang wekelijks data-gedreven ideeën trekt uit GSC,
    de Demand Engine en eerdere FB-engagement (mét terugkoppeling naar het
    live-artikel voor de FB→SEO-brug). Dat tweede, sterkere signaal werd tot nu
    toe alleen als logregel in het Actiecentrum getoond en nergens gebruikt om
    daadwerkelijk te posten. Retourneert None als geen enkele bron iets
    opleverde (geen GSC/Demand/FB-data) — de caller valt dan terug op de
    vault-gok, want zonder data is de vault nog altijd beter dan niets.
    """
    try:
        from ..domains.analytics import facebook_content as fbc
    except Exception as e:
        logger.debug("Datagedreven idee-module niet beschikbaar: %s", e)
        return None
    try:
        signals = fbc.gather_signals(project, days=28)
        if signals.get("error"):
            return None
        ideas = [i for i in fbc.build_ideas(signals, limit=10, site_name=project) if i.get("bron")]
    except Exception as e:
        logger.warning("Datagedreven idee-ophaal mislukt voor %s (val terug op vault): %s", project, e)
        return None
    if not ideas:
        return None
    # Voorkeur: een idee met een echt zoekwoord (GSC/Demand — al gesorteerd op
    # impressies binnen build_ideas), want dat geeft de LLM een concreet
    # onderwerp mét bewijs én een link-kans naar het live artikel. Een
    # "herhaal wat scoorde"-idee uit fb_engagement is alleen een korte
    # snippet van een oude post, geen volwaardig thema — bruikbaar als
    # laatste redmiddel, niet als eerste keus.
    for i in ideas:
        if i.get("query"):
            return i
    return ideas[0]


def _pick_theme(project: str) -> str:
    """Kies een thema uit de merkcontext (VaultReader) zodat posts variëren.

    Valt terug op de projectnaam als er geen vault-notities zijn. Deterministisch
    genoeg: we pakken een willekeurige (maar stabiele binnen de run) brand-noot.
    """
    try:
        from .vault_reader import VaultReader
        vr = VaultReader()
        if vr.is_configured:
            notes = vr.get_project_folder_notes(project)
            # Sla SKILL.md + index over; gebruik de eerste echte brand-noot.
            candidates = []
            for name, body in (notes or {}).items():
                if name.upper().startswith("SKILL") or "INDEX" in name.upper():
                    continue
                # Neem de eerste zin als ruwe thema-hint.
                first_line = (body or "").strip().splitlines()
                first_line = [l for l in first_line if l.strip() and not l.strip().startswith("#")]
                if first_line:
                    candidates.append(first_line[0][:80])
            if candidates:
                import random
                return random.choice(candidates)
    except Exception as e:
        logger.debug("Thema-keuze uit vault mislukt (projectnaam als fallback): %s", e)
    return f"verhaal van {project}"


async def run_auto_social(project: str) -> Dict:
    """Eén geplande run voor een project.

    1. Bepaal of auto-posten aanstaat + op welke platforms.
    2. Genereer een content-pack (posts + beeld + TikTok).
    3. Als auto_post AAN: keur goed + publiceer op de aangezette platforms.
       Als auto_post UIT: laat het pack achter de review-gate (pending_review).

    Retourneert een bondig verslag voor het Actiecentrum / scheduler-log.
    """
    from . import social_content as sc
    from .database import get_conn
    from .outcomes import log_outcome

    cfg = get_site_social_config(project)
    auto_post = bool(cfg and cfg.get("enabled"))
    platforms = (cfg or {}).get("platforms") or list(AUTO_PLATFORMS)

    idea = _pick_grounded_idea(project)
    if idea:
        theme = idea["werktitel"]
        angle = idea["hoek"] + " — " + idea["bewijs"]
        idea_source, idea_query = idea.get("bron", ""), idea.get("query") or ""
        idea_evidence, idea_url = idea.get("bewijs", ""), idea.get("url") or ""
    else:
        theme = _pick_theme(project)
        angle = ""
        idea_source, idea_query, idea_evidence, idea_url = "vault", "", "", ""

    result: Dict = {"project": project, "auto_post": auto_post, "theme": theme,
                    "idea_source": idea_source, "pack_id": None,
                    "published": {}, "manual": {}, "errors": []}

    try:
        pack = sc.generate_content_pack(
            project=project,
            theme=theme,
            angle=angle,
            platforms=platforms + ["linkedin", "tiktok"],  # altijd ook LI/TT in het pack
            with_image=True,
            with_video=True,
            idea_source=idea_source, idea_query=idea_query,
            idea_evidence=idea_evidence, idea_url=idea_url,
        )
    except Exception as e:
        logger.exception("Social auto: pack-generatie mislukt voor %s", project)
        result["errors"].append(f"generatie: {e}")
        try:
            log_outcome(project=project, action="social_auto_fout",
                        detail=f"Pack-generatie mislukt: {e}", status="error",
                        next_step="Controleer OpenModel-token en vault-context.")
        except Exception:
            pass
        return result

    result["pack_id"] = pack.id
    bron_label = ({"gsc_top_queries": "GSC-topquery", "demand_kansen": "Demand-kans",
                   "fb_engagement": "eerdere FB-engagement", "vault": "vault (geen data beschikbaar)"}
                  .get(idea_source, idea_source or "onbekend"))
    log_outcome(project=project, action="social_auto_pack",
                detail=f"Content-pack '{theme}' aangemaakt (bron: {bron_label}, auto_post={auto_post})",
                next_step=("Automatisch geplaatst — niets doen." if auto_post
                           else "Keur het pack goed in Social Creatie om te plaatsen."),
                status="ok")

    if not auto_post:
        # Alleen gegenereerd, wacht op menselijke goedkeuring.
        result["note"] = "auto_post uit — pack wacht op goedkeuring in Social Creatie"
        return result

    # HARD GUARD: alleen posten op de EIGEN pagina. Zonder eigen token in de
    # sites-tabel mag de engine niet terugvallen op de globale fallback (die van
    # een ander project kan zijn). Dan weigeren we en blijft het pack wachten.
    if not (cfg and cfg.get("has_own_token")):
        warn = ("Auto-post is AAN maar '" + project + "' heeft geen eigen Facebook/Instagram "
                "token in de sites-tabel. Om te voorkomen dat de post op de verkeerde pagina "
                "(de fallback van een ander project) belandt, is NIETS geplaatst. "
                "Koppel het eigen Page-token via de sites-tabel en zet auto-post opnieuw aan.")
        result["errors"].append("guard: geen eigen social-token — post geweigerd")
        result["note"] = warn
        try:
            log_outcome(project=project, action="social_auto_geblokkeerd",
                        detail=warn, status="error",
                        next_step="Vul facebook_page_id + facebook_page_token (en evt. instagram_business_id) "
                                  "voor dit project in de sites-tabel, daarna draait auto-post op de juiste pagina.")
        except Exception:
            pass
        return result

    # Auto-post aan: keur goed + publiceer.
    sc.approve_pack(pack.id)
    published, manual, errors = {}, {}, []
    for plat in platforms:
        try:
            res = await sc.publish_pack(pack.id, plat)
        except Exception as e:
            errors.append(f"{plat}: {e}")
            continue
        if res.get("manual") or res.get("error") == "manual":
            manual[plat] = res.get("detail", "Handmatig plaatsen (zie bestand)")
        elif res.get("success"):
            published[plat] = res.get("url", "geplaatst")
        else:
            errors.append(f"{plat}: {res.get('error', 'onbekend')}")
    result["published"] = published
    result["manual"] = manual
    result["errors"] = errors

    status = "ok" if (published or manual) else "error"
    try:
        log_outcome(project=project, action="social_auto_gepost",
                    detail=f"Auto-post {project}: {', '.join(published.keys()) or 'geen'} geplaatst"
                           + (f"; handmatig: {', '.join(manual.keys())}" if manual else ""),
                    status=status,
                    next_step=("Geen actie nodig." if status == "ok"
                               else "Controleer de social-tokens in de sites-tabel / .env."))
    except Exception:
        pass
    return result
