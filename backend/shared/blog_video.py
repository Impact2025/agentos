"""Blog → Video.

Maakt van een gepubliceerd/gereviewd blogartikel een korte verticale video
(9:16 short) met dezelfde wereldklasse-pipeline als Social Creatie: de agent
schrijft een eigen kort script vánuit het blog (geen letterlijke blog-tekst als
voice-over — dat leest houterig voor), Pexels levert het beeld op het onderwerp,
en render_short plakt er logo + serif + voice-over + muziek op.

De gegenereerde video wordt (optioneel) ook teruggekoppeld als SocialPack, zodat
hij in de Social Creatie-tab verschijnt met de bestaande Bekijk/Render-knoppen.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .database import get_conn
from . import video_render as vr
from .video_template import load_template
from . import social_content as sc

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent.parent


def _strip_html(html: str) -> str:
    """Zet blog-HTML om naar leesbare platte tekst (koptekens + alinea's)."""
    if not html:
        return ""
    # Verwijder script/style.
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    # Houd koppen/headers als aparte regels.
    html = re.sub(r"<h[1-6][^>]*>", "\n# ", html, flags=re.I)
    html = re.sub(r"</h[1-6]>", "\n", html, flags=re.I)
    html = re.sub(r"<li[^>]*>", "\n- ", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"<p[^>]*>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&[a-z]+;", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _write_script(title: str, blog_text: str, project: str) -> List[vr.Scene]:
    """Laat de LLM een kort, spreekbaar 4-scène script schrijven vánuit het blog."""
    voice = sc._brand_voice(project, project)
    excerpt = blog_text[:1800]
    system = (
        f"{voice}\n\n"
        "Je schrijft een kort, spreekbaar script voor een verticale video (9:16, ~30-40s) "
        "op basis van een blogartikel. GEEN letterlijke blog-tekst citeren — schrijf een "
        "eigen, emotionele vertaling die op het artikel is gebaseerd. "
        "Geef precies 4 scènes in dit formaat, elk met een header:\n"
        "HOOK: <één pakkende zin, max 12 woorden>\n"
        "BODY: <één zin, het kerninzicht, max 18 woorden>\n"
        "BODY: <één zin, een concreet voorbeeld of gevolg, max 18 woorden>\n"
        "CTA: <één zin met een zachte oproep, max 14 woorden>\n"
        "Schrijf in het Nederlands, actieve zinnen, alsof je het uitspreekt."
    )
    user = f"Titel van het blog: {title}\n\nInhoud van het blog:\n{excerpt}"
    raw = ""
    for attempt in range(3):
        try:
            raw = sc._sync_script_writer(system, user, max_tokens=400).strip()
        except Exception as e:
            logger.warning("Blog-script LLM poging %d mislukt: %s", attempt + 1, e)
            raw = ""
        if raw:
            break
        logger.info("Blog-script LLM leverde lege respons (poging %d), retry...", attempt + 1)
    scenes = _parse_script(raw, title)
    return scenes


def _parse_script(raw: str, title: str) -> List[vr.Scene]:
    """Parse het LLM-script naar Scene-objecten; deterministic fallback bij leeg."""
    if not raw:
        return [
            vr.Scene(narration=title, caption=title, kind="hook"),
            vr.Scene(narration=f"{title} — waarom het ertoe doet.", kind="body"),
            vr.Scene(narration="Dit is wat je eruit meeneemt.", kind="body"),
            vr.Scene(narration="Lees het hele verhaal op onze site.", caption="Lees verder", kind="cta"),
        ]
    # Split op elke header, ongeacht of er een newline vóór staat: het model
    # levert soms alles op één regel, of met markdown-sterretjes/streepjes ervoor
    # ("**HOOK:**", "- BODY:"). Met de oude `\n(?=HOOK:)`-split viel dan alles
    # ná de eerste header in één scène, en hield de video 2 scènes over.
    cleaned = re.sub(r"[*_`#>]+", "", raw)
    parts = re.split(r"(?:^|\n|\s)[-•\d.\s]*(?=(?:HOOK|BODY|CTA)\s*:)", cleaned, flags=re.I)
    out: List[vr.Scene] = []
    for part in parts:
        m = re.match(r"\s*(HOOK|BODY|CTA)\s*:\s*(.+)", part.strip(), flags=re.I | re.S)
        if not m:
            continue
        kind = m.group(1).lower()
        text = m.group(2).strip().replace("\n", " ")
        k = "hook" if kind == "hook" else ("cta" if kind == "cta" else "body")
        cap = text if k == "hook" else (text[:42] if k == "cta" else "")
        out.append(vr.Scene(narration=text, caption=cap, kind=k))
    if not out:
        return _parse_script("", title)
    # Zorg dat er altijd een CTA achterin zit.
    if out[-1].kind != "cta":
        out.append(vr.Scene(narration="Lees het hele artikel op onze site.", caption="Lees verder", kind="cta"))
    return out[:6]


def make_blog_video(job_id: str, project: str, title: str, blog_html: str,
                    *, register_pack: bool = True) -> Dict:
    """Render een video vánuit een blog en (optioneel) koppel terug als SocialPack.

    Retourneert een dict met success / video_url / video_path / scenes / error.
    """
    # De projectmap komt uit video_template._project_dir(): die kiest de map die
    # de template.json ÉCHT bevat (projects/ heeft dubbele spellingen als
    # 'liefde voor iedereen' vs 'liefdevooriedereen', en de UI stuurt soms
    # 'Liefde voor Iedereen'). Zonder dit maakte elke render een dérde map aan,
    # naast de map met logo/fonts/foto's — de video kwam dan buiten de
    # brand-assets terecht.
    from .video_template import _project_dir
    proj_dir = _project_dir(project)
    out_rel = f"projects/{proj_dir.name}/video/blog_{job_id}.mp4"
    out_path = _REPO / out_rel
    result: Dict = {"success": False, "job_id": job_id, "project": project}

    blog_text = _strip_html(blog_html)
    if not blog_text:
        # Zonder blogtekst valt terug op de titel als hook.
        blog_text = title

    try:
        scenes = _write_script(title, blog_text, project)
        tpl = load_template(project)
        res = vr.render_short(project, scenes, out_path, template=tpl)
    except Exception as e:  # noqa: BLE001
        logger.exception("Blog-video render mislukt voor %s", job_id)
        result["error"] = str(e)[:300]
        return result

    if not res.ok:
        result["error"] = res.error or "renderen mislukt"
        return result

    # Sla het pad op op de content-job (nieuwe kolom indien nodig).
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE content_jobs SET video_path=? WHERE id=?", (out_rel, job_id)
            )
    except Exception:
        # Kolom bestaat mogelijk niet — probeer aan te maken (idempotent).
        try:
            with get_conn() as conn:
                conn.execute("ALTER TABLE content_jobs ADD COLUMN video_path TEXT")
                conn.execute(
                    "UPDATE content_jobs SET video_path=? WHERE id=?", (out_rel, job_id)
                )
        except Exception as e2:
            logger.warning("video_path opslaan mislukt (negeerbaar): %s", e2)

    result.update(
        success=True,
        video_url=f"/api/content-queue/{job_id}/video",
        video_path=out_rel,
        duration=res.duration,
        scenes=res.scenes,
        attributions=res.attributions,
    )

    # Koppel terug als SocialPack zodat 'm in Social Creatie verschijnt.
    if register_pack:
        try:
            pack_id = f"blog_{job_id}"
            with get_conn() as conn:
                # Overschrijf nooit een bestaande pack.
                exists = conn.execute(
                    "SELECT 1 FROM social_posts WHERE id=?", (pack_id,)
                ).fetchone()
                if not exists:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO social_posts"
                        "(id, project, theme, angle, brand_context, copy_json, "
                        "image_brief_json, tiktok_pack_json, status, concept, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            pack_id, project, title, "Video gegenereerd uit blog",
                            project, json.dumps({}, ensure_ascii=False),
                            json.dumps({}, ensure_ascii=False),
                            json.dumps({}, ensure_ascii=False),
                            "pending", 0, now,
                        ),
                    )
                    # Koppel de video direct aan de pack.
                    conn.execute(
                        "UPDATE social_posts SET video_path=? WHERE id=?",
                        (out_rel, pack_id),
                    )
            result["social_pack_id"] = pack_id
        except Exception as e:
            logger.warning("SocialPack-registratie mislukt (video staat wel klaar): %s", e)

    return result
