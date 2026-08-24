"""Campagne-import — een uitgeschreven socialplan wordt een reeks review-klare packs.

Aanleiding (16 aug 2026). Het communicatie- en socialmediaplan voor
BewaardVoorJou telde achttien volledig uitgeschreven posts over zes weken,
compleet met beeldbriefs, hashtag-sets en posttijden. Het is **nooit uitgevoerd**.
Niet omdat het slecht was, maar omdat het een markdown-bestand in de
Downloads-map was: er was geen enkel mechanisme dat wist dat post 3.1 op maandag
19:30 hoorde te staan, dus kon ook niets melden dat hij er niet stond. Zes weken
lang leek er niets aan de hand.

Dat is exact de faalvorm die de rest van deze codebase bestrijdt (*"activiteit is
geen effect"*), een verdieping hoger: hier is het **plan** de activiteit zonder
effect. De reparatie is dus niet "de teksten ergens neerzetten" maar het plan een
plek geven waar een verstreken datum zichtbaar is — en daar hoort de invariant
`campagnepost_over_datum` bij, die precies dit meet.

Wat deze module wél en niet doet:

  - **Wel**: de posts uit het plan-bestand omzetten in `social_posts`-rijen met
    `pending_review`, een plaatsdatum, de hashtag-set uit het huisstijl-profiel
    en de beeld-brief mét het vaste stijlblok.
  - **Niet**: iets posten. De packs staan achter dezelfde review-gate als alles
    wat deze codebase naar buiten brengt.
  - **Niet**: de teksten herschrijven. Ze zijn met de hand geschreven en goed;
    ze door een model halen kost geld en levert een slechtere versie op. Alleen
    de hashtags worden toegevoegd, omdat die in het plan naar een set verwijzen
    ('[Set A]') die in `style.json` staat — twee administraties van dezelfde
    hashtags lopen gegarandeerd uit elkaar.

Idempotent: `(project, campaign, campaign_post)` is uniek, dus een tweede import
werkt bestaande, nog niet goedgekeurde packs bij in plaats van ze te verdubbelen.
Een pack dat al goedgekeurd of geplaatst is wordt nooit aangeraakt — dan zou een
herimport een menselijk besluit terugdraaien.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import social_style
from .database import get_conn

logger = logging.getLogger(__name__)

CAMPAGNE_RELPATH = "social/campagne-6weken.json"

# Statussen waarin een mens al iets besloten heeft. Die overschrijven we nooit.
_ONAANRAAKBAAR = ("approved", "posted", "rejected")


def _campagne_pad(project: str, bestand: str = CAMPAGNE_RELPATH) -> Optional[Path]:
    """Zoek het plan-bestand; hergebruikt de spelling-tolerante zoektocht van style."""
    direct = social_style.REPO_ROOT / "projects" / project / bestand
    if direct.exists():
        return direct
    root = social_style.REPO_ROOT / "projects"
    if not root.is_dir():
        return None
    doel = social_style._norm(project)
    for d in sorted(root.iterdir()):
        if d.is_dir() and social_style._norm(d.name) == doel:
            cand = d / bestand
            if cand.exists():
                return cand
    return None


def _eerstvolgende_maandag(vanaf: date) -> date:
    """De maandag op of na `vanaf`.

    Het plan rekent in weken die op maandag beginnen (ma emotie / wo product /
    vr activatie). Halverwege de week starten zou week 1 tot één post inkorten
    zonder dat iemand dat vraagt.
    """
    return vanaf + timedelta(days=(7 - vanaf.weekday()) % 7)


def plan_datums(posts: List[Dict], start: date) -> Dict[str, datetime]:
    """Reken week+weekdag+tijd om naar echte datumtijden vanaf een startmaandag."""
    uit: Dict[str, datetime] = {}
    for p in posts:
        try:
            week = int(p.get("week", 1))
            dag = int(p.get("dag", 0))
            uur, minuut = (p.get("tijd") or "12:00").split(":")
            dt = datetime.combine(
                start + timedelta(weeks=week - 1, days=dag),
                datetime.min.time(),
            ).replace(hour=int(uur), minute=int(minuut))
            uit[str(p.get("id"))] = dt
        except (TypeError, ValueError) as e:
            logger.warning("Campagnepost %s heeft een onbruikbaar moment (%s)", p.get("id"), e)
    return uit


def _beeld_brief(post: Dict, style: social_style.SocialStyle) -> Dict:
    """Bouw de beeld-brief uit het plan + het vaste stijlblok van het merk."""
    from .social_content import ImageBrief, _dimensions_for

    ov = style.overlay
    brief = ImageBrief(
        template_type="quote-card",
        dimensions=_dimensions_for(style.aspect),
        headline=(post.get("titel") or "")[:80],
        subtext=(post.get("onderschrift") or "")[:160],
        midjourney_prompt=style.image_prompt(post.get("midjourney") or post.get("titel") or ""),
        layout=(
            "Gouden serif-titel op een donker transparant vlak in het onderste derde deel, "
            "wit serif-onderschrift eronder"
            + (f", logo {ov.logo_positie}" if ov.logo_path else "")
            + (f", '{ov.footer_tekst}' onderaan." if ov.footer_tekst else ".")
        ),
    )
    if ov.footer_tekst:
        brief.canva_note = (
            f"Beeld volgens het vaste stijlblok; tekst in Canva: gouden serif-titel + "
            f"wit serif-onderschrift op donker transparant vlak, logo {ov.logo_positie}, "
            f"{ov.footer_tekst} onderaan."
        )
    return asdict(brief)


def _video_pack(post: Dict) -> Dict:
    """Zet een videoconcept uit het plan om in het bestaande TikTok-packveld.

    Niet elk plan heeft bewegend beeld en niet elke post binnen een plan verdient
    een film — het Bijeen-videoplan levert er zes bij achttien posts. Zonder dit
    blok verdwijnen de Veo-prompts bij het importeren, en dan staat de tekst wél
    in Impact OS en het beeldplan weer alleen in een bestand: precies de splitsing
    die deze module moest opheffen.

    De prompts gaan onbewerkt mee. Een generatieprompt is een recept waarvan één
    gewijzigd woord een heel ander resultaat geeft; hem 'netter' maken is hem
    kapotmaken.
    """
    video = post.get("video") or {}
    if not isinstance(video, dict) or not video:
        return {}
    shots = [s for s in (video.get("veo_prompt"), video.get("veo_prompt_2")) if s]
    return {
        "hook": str(video.get("concept") or ""),
        "script": str(video.get("veo_prompt") or ""),
        "shotlist": shots,
        "voiceover_cues": str(video.get("edit") or ""),
        "captions": str(post.get("titel") or ""),
        "hashtags": [],
        "duration_sec": 30,
        # Het kanaal en het doel horen bij de montage, niet bij de muziek — maar
        # dit is het enige vrije veld dat de bestaande UI toont, en weglaten zou
        # betekenen dat 'IG Reel 9:16' nergens meer staat.
        "music_cue": " · ".join(x for x in (video.get("kanaal"), video.get("doel")) if x),
    }


def _angle(post: Dict) -> str:
    """De invalshoek plus alles wat een mens vlák voor het plaatsen moet weten.

    `budget` en `notitie` staan bewust hier en niet in `idea_evidence`: dat veld
    betekent 'waaróm is dit onderwerp gekozen', en er een advertentiebudget of
    een kanaalregel in stoppen maakt elke latere herkomst-analyse onbetrouwbaar.
    Een kanaalregel als "zet de link in de eerste reactie" is precies het soort
    afspraak dat in een document verdwijnt; naast de tekst blijft hij staan.
    """
    delen = [post.get("onderschrift") or ""]
    if post.get("notitie"):
        delen.append(f"Let op: {post['notitie']}")
    if post.get("budget"):
        delen.append(f"Betaalde inzet: {post['budget']}")
    return "\n\n".join(d for d in delen if d).strip()


def importeer_campagne(project: str, *, start: Optional[date] = None,
                       bestand: str = CAMPAGNE_RELPATH,
                       vernieuw_bestaande: bool = True) -> Dict:
    """Zet het plan om in packs. Retourneert een telling per uitkomst.

    `start` is de maandag waarop week 1 begint; standaard de eerstvolgende
    maandag. De datums in het plan-bestand zijn relatief (week + weekdag + tijd)
    juist omdat de oorspronkelijke campagnedatums verstreken zijn — een plan dat
    alleen met absolute datums werkt, is na één gemiste periode onbruikbaar.
    """
    pad = _campagne_pad(project, bestand)
    if pad is None:
        return {"success": False, "error": f"Geen campagnebestand voor {project} ({bestand})"}
    try:
        data = json.loads(pad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"success": False, "error": f"Campagnebestand onleesbaar: {e}"}

    posts = data.get("posts") or []
    if not posts:
        return {"success": False, "error": "Campagnebestand bevat geen posts"}

    campagne = str(data.get("campagne") or pad.stem)
    style = social_style.load_style(project)
    start = start or _eerstvolgende_maandag(date.today())
    momenten = plan_datums(posts, start)

    nieuw, bijgewerkt, overgeslagen = 0, 0, []
    from .social_content import apply_hashtags

    with get_conn() as conn:
        for post in posts:
            pid = str(post.get("id") or "")
            moment = momenten.get(pid)
            if not pid or moment is None:
                overgeslagen.append(pid or "(zonder id)")
                continue

            post_type = str(post.get("type") or "")
            copy = {k.lower(): v for k, v in (post.get("copy") or {}).items() if v}
            copy = apply_hashtags(copy, project, post_type)
            brief = _beeld_brief(post, style)
            thema = post.get("titel") or pid
            angle = _angle(post)
            video = _video_pack(post)

            bestaand = conn.execute(
                "SELECT id, status FROM social_posts "
                "WHERE project=? AND campaign=? AND campaign_post=?",
                (project, campagne, pid),
            ).fetchone()

            if bestaand:
                if bestaand["status"] in _ONAANRAAKBAAR or not vernieuw_bestaande:
                    overgeslagen.append(f"{pid} ({bestaand['status']})")
                    continue
                conn.execute(
                    "UPDATE social_posts SET theme=?, angle=?, copy_json=?, "
                    "image_brief_json=?, tiktok_pack_json=?, scheduled_for=?, "
                    "post_type=? WHERE id=?",
                    (thema, angle, json.dumps(copy, ensure_ascii=False),
                     json.dumps(brief, ensure_ascii=False),
                     json.dumps(video, ensure_ascii=False), moment.isoformat(),
                     post_type, bestaand["id"]),
                )
                bijgewerkt += 1
                continue

            conn.execute(
                "INSERT INTO social_posts(id, project, theme, angle, brand_context, "
                "copy_json, image_brief_json, tiktok_pack_json, status, concept, "
                "created_at, origin, idea_source, idea_query, idea_evidence, idea_url, "
                "campaign, campaign_post, scheduled_for, post_type) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"sp_{uuid.uuid4().hex[:12]}", project, thema, angle, project,
                 json.dumps(copy, ensure_ascii=False),
                 json.dumps(brief, ensure_ascii=False),
                 json.dumps(video, ensure_ascii=False),
                 "pending_review", 0, datetime.now().isoformat(),
                 # origin='campagne' onderscheidt deze packs van de gegenereerde
                 # (origin='pipeline') en de handmatige (origin='deluxe_manual')
                 # in hetzelfde ledger. idea_url is de link waar de post naartoe
                 # stuurt — dat voedt dezelfde FB→SEO-meetlus als de rest.
                 "campagne", "campagne_plan", "", "", style.site_url,
                 campagne, pid, moment.isoformat(), post_type),
            )
            nieuw += 1

    resultaat = {
        "success": True,
        "project": project,
        "campagne": campagne,
        "start": start.isoformat(),
        "eind": max(momenten.values()).date().isoformat() if momenten else start.isoformat(),
        "nieuw": nieuw,
        "bijgewerkt": bijgewerkt,
        "overgeslagen": overgeslagen,
        "totaal": len(posts),
    }

    try:
        from .outcomes import log_outcome
        log_outcome(
            project=project,
            action="campagne_geimporteerd",
            detail=(f"Socialplan '{campagne}': {nieuw} nieuwe en {bijgewerkt} bijgewerkte posts, "
                    f"ingepland {resultaat['start']} t/m {resultaat['eind']}"),
            artifact=str(pad),
            next_step="Open Social Creatie, keur per post goed en plaats hem op het kanaal.",
            status="ok",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("log_outcome (campagne) mislukt: %s", e)

    return resultaat


def agenda(project: str, campagne: str = "") -> List[Dict]:
    """De campagne-posts op volgorde van plaatsdatum, met status.

    Dit is wat een socialplan mist zodra het een markdown-bestand is: één blik
    waarin te zien is wat er klaarstaat, wat er geplaatst is, en wat er over de
    datum heen is.
    """
    with get_conn() as conn:
        q = ("SELECT campaign_post, campaign, theme, post_type, status, scheduled_for, id "
             "FROM social_posts WHERE project=? AND campaign<>''")
        params: List = [project]
        if campagne:
            q += " AND campaign=?"
            params.append(campagne)
        rows = conn.execute(q + " ORDER BY scheduled_for", params).fetchall()
    nu = datetime.now()
    uit = []
    for r in rows:
        gepland = r["scheduled_for"] or ""
        over_datum = False
        if gepland and r["status"] == "pending_review":
            try:
                over_datum = datetime.fromisoformat(gepland) < nu
            except ValueError:
                over_datum = False
        uit.append({
            "pack_id": r["id"],
            "post": r["campaign_post"],
            "campagne": r["campaign"],
            "titel": r["theme"],
            "type": r["post_type"],
            "status": r["status"],
            "gepland": gepland,
            "over_datum": over_datum,
        })
    return uit
