"""
Iris-onboarding — de vier intake-stappen en het afsluiten ervan.

Elke stap schrijft direct naar de tabel waar dat veld al hoort (geen aparte
kopie van dezelfde waarheid): bedrijfsdoel -> sites.profile, schrijfstijl ->
iris_knowledge (scope=project), autonomie -> project_autonomy. Stap 3
(kanalen) heeft geen eigen schrijfactie — die status komt uit `oauth_accounts`
via oauth_microsoft/oauth_google.

`site_id` is de sleutel voor alles hier (zelfde als sites_router.py); waar een
tabel op projectnaam matcht (iris_knowledge.scope, project_autonomy.project)
gebruiken we `site["name"]` — de canonieke schrijfwijze volgens
shared/projects.py, niet een los ingetypte string.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ..seo import sites as sites_service
from ..iris import knowledge as iris_knowledge
from . import oauth_google, oauth_microsoft

# Zelfde drempel als seo/engine.py:cold_start_opportunities — een profiel
# korter dan dit is voor de contentmotor al net zo goed leeg.
MIN_PROFILE_LENGTH = 40

AUTONOMY_PRESETS: Dict[str, Dict[str, int]] = {
    "laag":    {"content_run_max": 1, "outreach_max": 5,  "seo_refresh_max": 1, "linkbuild_max": 3},
    "normaal": {"content_run_max": 2, "outreach_max": 10, "seo_refresh_max": 1, "linkbuild_max": 6},
    "hoog":    {"content_run_max": 3, "outreach_max": 15, "seo_refresh_max": 2, "linkbuild_max": 10},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_site(site_id: str) -> Dict[str, Any]:
    site = sites_service.get_site(site_id)
    if site:
        return site
    # Geen SEO-site gevonden — voor een tenant-eigen klant (bijv. Nicole, die
    # geen SEO-site heeft maar wél een tenant) creëren we een virtuele
    # site-rij op basis van de site_id (== tenant-slug). Zo werkt de volledige
    # onboarding-wizard (bedrijfsdoel, schrijfstijl, autonomie) ook zonder een
    # echte site. De wizard stuurt alleen geldige site_id's of de tenant-slug,
    # dus dit is veilig — geen willekeurige/lege id's.
    if not site_id or not str(site_id).strip():
        raise ValueError(f"Onbekende site: {site_id}")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sites (id, name, created_at) VALUES (?, ?, ?)",
            (site_id, str(site_id).strip(), _now()),
        )
    site = sites_service.get_site(site_id)
    if not site:
        raise ValueError(f"Onbekende site: {site_id}")
    return site


# ── Stap 1: bedrijfsdoel & prioriteiten ─────────────────────────────────────

def save_step1(site_id: str, profile: str) -> Dict[str, Any]:
    site = _require_site(site_id)
    sites_service.update_site(site_id, {"profile": (profile or "").strip()})
    return get_status(site_id)


# ── Stap 2: schrijfstijl & merkstem ─────────────────────────────────────────

async def save_step2(site_id: str, tone_text: str) -> Dict[str, Any]:
    site = _require_site(site_id)
    text = (tone_text or "").strip()
    if len(text) < 20:
        raise ValueError("Beschrijf de schrijfstijl in minstens een paar zinnen (min. 20 tekens).")
    await iris_knowledge.add_manual_note(
        title=f"Schrijfstijl — {site['name']}",
        text=text,
        scope=site["name"],
    )
    return get_status(site_id)


# ── Stap 3: kanalen — geen schrijfactie, alleen status (zie get_status) ────


def disconnect_channel(site_id: str, provider: str) -> bool:
    _require_site(site_id)
    if provider == "microsoft":
        return oauth_microsoft.disconnect(site_id)
    if provider == "google":
        return oauth_google.disconnect(site_id)
    raise ValueError(f"Onbekende provider: {provider}")


# ── Stap 4: werk-grenzen & autonomie ────────────────────────────────────────

def save_step4(site_id: str, preset: str, overrides: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    site = _require_site(site_id)
    if preset not in AUTONOMY_PRESETS:
        raise ValueError(f"Onbekende preset: {preset} (kies laag/normaal/hoog)")
    values = dict(AUTONOMY_PRESETS[preset])
    if overrides:
        for key in ("content_run_max", "outreach_max", "seo_refresh_max", "linkbuild_max"):
            if key in overrides and overrides[key] is not None:
                values[key] = max(1, int(overrides[key]))
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_autonomy
               (project, content_run_max, outreach_max, seo_refresh_max, linkbuild_max, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(project) DO UPDATE SET
                   content_run_max = excluded.content_run_max,
                   outreach_max    = excluded.outreach_max,
                   seo_refresh_max = excluded.seo_refresh_max,
                   linkbuild_max   = excluded.linkbuild_max,
                   updated_at      = excluded.updated_at""",
            (site["name"], values["content_run_max"], values["outreach_max"],
             values["seo_refresh_max"], values["linkbuild_max"], now),
        )
    return get_status(site_id)


# ── Status & afronden ────────────────────────────────────────────────────────

def get_status(site_id: str) -> Dict[str, Any]:
    site = _require_site(site_id)
    ms = oauth_microsoft.account_info(site_id)
    gg = oauth_google.account_info(site_id)
    with get_conn() as conn:
        knowledge_rows = conn.execute(
            "SELECT id FROM iris_knowledge WHERE active=1 AND lower(scope)=lower(?) AND source='manual' "
            "AND title LIKE 'Schrijfstijl —%'",
            (site["name"],),
        ).fetchall()
        autonomy = conn.execute(
            "SELECT * FROM project_autonomy WHERE project = ?", (site["name"],),
        ).fetchone()

    step1_done = len((site.get("profile") or "").strip()) >= MIN_PROFILE_LENGTH
    step2_done = bool(knowledge_rows)
    step4_done = autonomy is not None

    return {
        "site_id": site_id,
        "project": site["name"],
        "onboarded_at": site.get("onboarded_at"),
        "steps": {
            "1_bedrijfsdoel": {
                "done": step1_done,
                "profile": site.get("profile") or "",
                "min_length": MIN_PROFILE_LENGTH,
            },
            "2_schrijfstijl": {"done": step2_done},
            "3_kanalen": {
                "microsoft": ms,
                "google": gg,
                "microsoft_configured": oauth_microsoft.is_configured(),
                "google_configured": oauth_google.is_configured(),
            },
            "4_autonomie": {
                "done": step4_done,
                "current": dict(autonomy) if autonomy else None,
                "presets": AUTONOMY_PRESETS,
            },
        },
        "ready_to_complete": step1_done and step2_done and step4_done,
    }


def complete_onboarding(site_id: str) -> Dict[str, Any]:
    status = get_status(site_id)
    missing: List[str] = []
    if not status["steps"]["1_bedrijfsdoel"]["done"]:
        missing.append(f"bedrijfsdoel (minstens {MIN_PROFILE_LENGTH} tekens)")
    if not status["steps"]["2_schrijfstijl"]["done"]:
        missing.append("schrijfstijl")
    if not status["steps"]["4_autonomie"]["done"]:
        missing.append("werk-grenzen")
    if missing:
        raise ValueError(
            "Onboarding is nog niet compleet — ontbreekt: " + ", ".join(missing) + ". "
            "Ik sluit dit bewust niet stil af met een half ingevuld profiel."
        )
    with get_conn() as conn:
        conn.execute("UPDATE sites SET onboarded_at = ? WHERE id = ?", (_now(), site_id))
    return get_status(site_id)
