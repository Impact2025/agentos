"""Actiecentrum — één inbox met alles wat op een menselijke beslissing wacht.

Antwoordt op de drie vragen die het dashboard eerder niet beantwoordde:
  1. Wat moet ik (Vincent) nú doen?          → items met needs_you=True
  2. Wat ging er mis en vereist mijn actie?  → items kind='error'
  3. Wat is er gebeurd?                      → de uitkomst-feed (activity_log)

Elk item heeft `actions`: knoppen die de frontend 1-op-1 vertaalt naar
bestaande endpoints. Het Actiecentrum voert zelf niets uit — het verzamelt.
"""
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ..mail.gsc_expert import is_gsc_mail as _is_gsc_mail
from ..publish import content_pipeline


# ── Project-normalisatie ────────────────────────────────────────────────────
# De inbox mixt twee naamruimten: items dragen óf de vault-projectnaam
# (goals, mail, social — bv. "Bewaard voor Jou"), óf de site-naam
# (content_jobs — bv. "DatingAssistent 40+"), en errors/campagnes kunnen een
# "goal:<id>"-sleutel dragen. Die twee lopen uiteen: een site "DatingAssistent
# 40+" hoort bij project "DatingAssistent", en "Daar" bij "daarwebsite". Een
# domme `item.project == P`-filter mist daardoor de content-wachtrijen van een
# project. We normaliseren alles naar één canonieke sleutel (lowercase,
# alleen alfanumeriek) en lossen site→project op via prefix-matching.
def _norm_project_key(name: str) -> str:
    """Canonieke sleutel voor een project-/site-naam: lowercase, geen
    leestekens of spaties — zodat "Bewaard voor Jou" == "bewaardvoorjou"."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _build_site_to_project(conn) -> Dict[str, Optional[str]]:
    """Map elke site-naam naar de canonieke project-sleutel waartoe hij hoort.

    Regel: de genormaliseerde site-naam is gelijk aan, of een prefix van, de
    genormaliseerde project-naam (of omgekeerd). "DatingAssistent 40+" →
    "datingassistent"; "Daar" → "daarwebsite". Bij meerdere kandidaten wint de
    langste overlap. Project-sleutels komen uit goals.project + de vault
    (via _scan_projects), zodat we alleen aan échte projecten koppelen.
    """
    project_keys: set = set()
    for row in conn.execute("SELECT DISTINCT project FROM goals"):
        p = row["project"]
        if p and p not in ("all", "Globaal"):
            project_keys.add(_norm_project_key(p))
    try:
        from ...domains.projects.router import _scan_projects
        for p in _scan_projects():
            name = (p.get("name") or "").strip()
            if name and not name.startswith("_"):
                project_keys.add(_norm_project_key(name))
    except Exception:
        pass

    out: Dict[str, Optional[str]] = {}
    for row in conn.execute("SELECT DISTINCT name FROM sites"):
        site = row["name"] or ""
        sn = _norm_project_key(site)
        best: Optional[str] = None
        best_score = 0
        for pk in project_keys:
            if sn == pk:
                best = pk
                best_score = 999
                break
            if sn.startswith(pk) or pk.startswith(sn):
                score = min(len(sn), len(pk))
                if score > best_score:
                    best = pk
                    best_score = score
        out[site] = best
    return out


def _resolve_item_project(project_field: Optional[str]) -> Optional[str]:
    """Canonieke project-sleutel achter een inbox-item zijn `project`-veld.

    - "goal:<id>" → echte project uit goals.
    - site-naam → project via de site→project-map.
    - reeds een project-naam → die sleutel.
    - iets anders (Agenda, Leads, Scheduler, …) → None (niet project-gebonden).

    Gebruikt de module-global project-index (eigen connectie, gecached) zodat
    deze functie nooit leunt op een outer connectie die al gesloten kan zijn.
    """
    global _PROJECT_INDEX
    if _PROJECT_INDEX is None:
        _PROJECT_INDEX = _build_project_index()
    known, site_to_proj, goal_to_proj = _PROJECT_INDEX
    if not project_field or project_field == "?":
        return None
    if project_field.startswith("goal:"):
        return goal_to_proj.get(project_field)
    n = _norm_project_key(project_field)
    if n in known:
        return n
    return site_to_proj.get(project_field)



# WeAreImpact is niet zomaar een klantproject maar Vincents eigen bedrijf —
# de plek waar zijn agenda, leads en scheduler-fouten al horen. Items zónder
# resolveerbaar project (Agenda, Leads, Scheduler, …) horen daarom bij WÉL
# WeAreImpact's eigen dashboard, en bij geen enkel ander (klant)project.
_WEAREIMPACT_KEY = "weareimpact"


def _item_belongs_to_project(project_field: Optional[str], target_key: str) -> bool:
    """Klopt dit item bij het gevraagde project (canonieke sleutel)?

    Vóór 23 aug 2026 vielen niet-project-gebonden items (Agenda-voorstellen,
    Leads, Scheduler-fouten — alle drie expliciet 'None' in
    `_resolve_item_project`) uit ELKE per-project inbox, óók die van
    WeAreImpact zelf: een WhatsApp-afspraakvoorstel voor 24 augustus stond
    wél in de globale Control Room-inbox (project=None) maar toonde "0" op
    het WeAreImpact-dashboard, waar Vincent 'm juist verwachtte af te
    handelen. De Agenda-tab kende deze uitzondering al (zichtbaar op
    WeAreImpact, verborgen op klantprojecten) — deze filter volgt nu
    dezelfde regel."""
    resolved = _resolve_item_project(project_field)
    if resolved is None and target_key == _WEAREIMPACT_KEY:
        return True
    return resolved == target_key


# Gecachte project-index (known-keys + site→project + goal→project). Eén keer
# gebouwd per proces; bij herstart/redeploy vers. Projecten veranderen zelden,
# dus een statische cache is hier veilig genoeg.
_PROJECT_INDEX: Optional[tuple] = None


def _build_project_index() -> tuple:
    """Bouw (known_keys, site_to_proj, goal_to_proj) met een eigen connectie.

    - known_keys: genormaliseerde namen van alle echte projecten (goals +
      vault), zodat een item met project-veld == projectnaam direct matcht.
    - site_to_proj: site-naam → genormaliseerde project-sleutel.
    - goal_to_proj: "goal:<id>" → genormaliseerde project-sleutel.
    """
    from ...domains.projects.router import _scan_projects

    with get_conn() as conn:
        known: set = set()
        for row in conn.execute("SELECT DISTINCT project FROM goals"):
            p = row["project"]
            if p and p not in ("all", "Globaal"):
                known.add(_norm_project_key(p))
        for p in _scan_projects():
            name = (p.get("name") or "").strip()
            if name and not name.startswith("_"):
                known.add(_norm_project_key(name))
        site_to_proj = _build_site_to_project(conn)
        goal_to_proj = {}
        for row in conn.execute("SELECT id, project FROM goals"):
            if row["project"]:
                goal_to_proj["goal:" + row["id"]] = _norm_project_key(row["project"])
    return known, site_to_proj, goal_to_proj


def _short_title(title: str) -> str:
    """Kaarttitel: een kop, geen alinea. Sommige oudere jobs kregen een hele
    alinea als titel (zie `content_pipeline._clean_title`); die mag de kaart
    niet overspoelen."""
    return content_pipeline._clean_title(title or "") or "(zonder titel)"

# Ruwe activity_log-actienamen zijn code, geen kaarttitel ("task_not_executed
# — goal:goal-20260810-194752-608bdd81d0d6" zegt niets over wát er misging).
_ERROR_ACTION_LABELS = {
    "task_not_executed": "Taak niet uitgevoerd",
    "task_failed": "Taak mislukt",
    "live-fout": "Publicatie niet zichtbaar",
    "publish-fout": "Publiceren mislukt",
    "live-overgeslagen": "Publicatiecontrole overgeslagen",
    "job_nooit_geslaagd": "Taak nog nooit geslaagd",
}


def _display_project(conn, project: str) -> str:
    """Een goal-fout draagt `project='goal:<id>'` (zie goal/service.py:_log_activity)
    — dat is een sleutel, geen naam. Overal waar het project getoond wordt
    (kaarttitel én de losse project-regel in Iris Remote) hoort het echte
    projectbord te staan."""
    if project.startswith("goal:"):
        goal_id = project[len("goal:"):]
        row = conn.execute("SELECT title, project FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row:
            return row["project"] or row["title"] or goal_id
        return goal_id
    return project


def _error_card_title(conn, e: Dict[str, Any]) -> str:
    """Mensleesbare titel voor een foutkaart: welke taak, van welk doel/project
    — niet de ruwe actienaam naast een goal-id."""
    label = _ERROR_ACTION_LABELS.get(e["action"]) or e["action"].replace("_", " ").capitalize()
    project = e["project"] or ""
    detail = e["detail"] or ""
    # Taaktitel staat vaak vooraan in de detailtekst tussen aanhalingstekens:
    # "'Content-editing gids-artikel' na 4 pogingen: ...".
    task_title = None
    if detail.startswith("'"):
        end = detail.find("'", 1)
        if end > 1:
            task_title = detail[1:end]
    if project.startswith("goal:"):
        goal_id = project[len("goal:"):]
        row = conn.execute("SELECT title, project FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row:
            goal_title = row["title"] or goal_id
            where = row["project"] or goal_title
            if task_title:
                return f"{label}: '{task_title}' — doel '{goal_title}' ({where})"
            return f"{label} — doel '{goal_title}' ({where})"
        return f"{label} — {goal_id}"
    if task_title:
        return f"{label}: '{task_title}' — {project}" if project else f"{label}: '{task_title}'"
    return f"{label} — {project}" if project else label

logger = logging.getLogger(__name__)
VACANCY_FIT_THRESHOLD = 60

# Fouten ouder dan dit aantal dagen vervallen vanzelf uit de inbox.
ERROR_WINDOW_DAYS = 3

# De SPA pollt dit endpoint elke ~30s; de onder-de-grens-WARNING per job
# daarom max 1×/uur loggen in plaats van bij elke poll (log-spam).
_LOW_SCORE_WARNED: Dict[str, float] = {}
_LOW_SCORE_WARN_INTERVAL = 3600.0


def _dismissed(conn) -> set:
    return {
        (r["kind"], r["ref_id"])
        for r in conn.execute("SELECT kind, ref_id FROM inbox_dismissals")
    }


def _json_list(v) -> list:
    """Parse een JSON-string-veld (tags/contracts) naar een list, robuust."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def _json_dict(v) -> dict:
    """Parse een JSON-string-veld naar een dict, robuust (spiegelt _json_list)."""
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    try:
        out = json.loads(v)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _snippet(text: str, max_len: int = 220) -> str:
    """Korte, leesbare snippet zonder quoting/HTML-ruis."""
    import re as _re
    t = _re.sub(r"\s+", " ", text or "").strip()
    return (t[:max_len] + "…") if len(t) > max_len else t


def _is_known_sender(conn, from_addr: str) -> bool:
    """Heeft Vincent deze afzender ooit als 'bekend' gemarkeerd in het
    Actiecentrum? Zo ja, dan is hij géén 'Nieuwe afzender' meer — ook al
    staat hij niet in de CRM/leads-tafel. Het register is handmatig beheerd
    via de 'Markeer als bekend'-knop; het is de plek waar Vincent bijhoudt
    wie een contact is (bv. 10x-hire) zónder dat er een lead-rij nodig is.
    """
    if not from_addr:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM known_senders WHERE lower(addr)=?",
            (from_addr,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _mark_sender_known(conn, from_addr: str, name: str = "") -> None:
    """Voeg een afzender toe aan het bekende-afzenders-register (idempotent)."""
    if not from_addr:
        return
    conn.execute(
        "INSERT OR IGNORE INTO known_senders (addr, name, created_at) VALUES (?, ?, datetime('now'))",
        (from_addr.strip().lower(), (name or "").strip()),
    )


def _classify_sender(conn, from_addr: str) -> Dict[str, Any]:
    """Bepaal of een afzender al bekend is in ons systeem (klant/lead) of een
    nieuw contact is. Kijkt in leads, radar_watchlist, mail_ignored_senders en
    of er eerdere mails/afspraken van dit adres bestaan.

    Returns dict met: known, label, where, tier, en (optioneel) crm = de volle
    lead-rij zodat de detail-modal de CRM-data kan tonen.
    """
    if not from_addr:
        return {"known": False, "label": "Onbekend", "where": [], "tier": "new", "crm": None}
    where = []
    crm_row = None

    # 1) CRM / leads
    try:
        lead = conn.execute(
            "SELECT * FROM leads WHERE lower(email)=?", (from_addr,)
        ).fetchone()
        if lead:
            crm_row = dict(lead)
            where.append(f"lead (#{lead['id']}, status: {lead['status']})")
    except Exception:
        pass

    # 2) Radar-watchlist / prospect-targets
    try:
        rad = conn.execute(
            "SELECT id, project, keyword FROM radar_watchlist "
            "WHERE lower(keyword) LIKE ? OR lower(?) LIKE '%' || lower(keyword) || '%'",
            (f"%{from_addr}%", from_addr),
        ).fetchall()
        for r in rad:
            where.append(f"radar ({r['project']})")
    except Exception:
        pass

    # 3) Historie: eerdere mails of al eerder afgehandelde voorstellen?
    try:
        mail_n = conn.execute(
            "SELECT COUNT(*) AS n FROM mail_inbox WHERE lower(from_addr)=?",
            (from_addr,),
        ).fetchone()["n"]
        prop_n = conn.execute(
            "SELECT COUNT(*) AS n FROM calendar_proposals WHERE lower(from_addr)=? "
            "AND status IN ('booked','rejected')",
            (from_addr,),
        ).fetchone()["n"]
        if mail_n > 1 or prop_n > 0:
            where.append(f"{mail_n} eerdere mail(s), {prop_n} afgehandeld voorstel")
    except Exception:
        pass

    # 4) Geïgnoreerde afzenders
    try:
        ign = conn.execute(
            "SELECT 1 FROM mail_ignored_senders WHERE lower(from_addr)=?",
            (from_addr,),
        ).fetchone()
        if ign:
            where.append("op ignoreer-lijst")
    except Exception:
        pass

    # Nuanceer: iemand kan al mailhistorie hebben (bekend contact) zónder dat
    # hij in de CRM/leads-tafel staat. Dan is het wél een nieuwe *lead* voor
    # Vincent's diensten — maar géén volslagen onbekende. We onderscheiden drie
    # niveaus zodat de badge op de kaart de juiste kleur krijgt.
    on_ignore = "op ignoreer-lijst" in where
    in_crm = crm_row is not None or any(w.startswith("radar (") for w in where)
    has_history = any("mail" in w or "voorstel" in w for w in where)
    if on_ignore:
        return {"known": False, "label": "Geïgnoreerde afzender", "where": where, "tier": "ignored", "crm": crm_row}
    if in_crm:
        return {"known": True, "label": "Bekende klant/lead (CRM)", "where": where, "tier": "crm", "crm": crm_row}
    if has_history:
        return {"known": False, "label": "Warm contact — nog geen lead", "where": where, "tier": "warm", "crm": crm_row}
    return {"known": False, "label": "Nieuwe lead", "where": where, "tier": "new", "crm": crm_row}


def _calendar_detail(conn, p: Dict) -> Dict[str, Any]:
    """Verrijk een calendar_proposal met lead-CRM-data en de laatste 3
    gerelateerde mails, zodat de detail-modal volledige context geeft i.p.v.
    kale statistieken.

    Returns het bestaande `detail`-dict, aangevuld met: lead (dict of None),
    recent_mails (list van {date, subject, snippet}), obsidian_path.
    """
    from_addr = (p.get("from_addr") or "").strip().lower()
    detail = {
        "from_addr": p.get("from_addr"),
        "subject": p.get("subject"),
        "proposed_start": p.get("proposed_start"),
        "proposed_end": p.get("proposed_end"),
        "location": p.get("location"),
        "is_remote": bool(p.get("is_remote")),
        "duration_min": p.get("duration_min"),
        "travel_buffer_min": p.get("travel_buffer_min"),
        "priority": p.get("priority"),
        "rationale": (p.get("rationale") or "").strip(),
        "mailbox_id": p.get("mailbox_id"),
        "inbox_id": p.get("inbox_id"),
    }
    lead_status = _classify_sender(conn, from_addr)
    detail["lead_status"] = lead_status

    # Lead-CRM-data (als er een lead is).
    crm = lead_status.get("crm")
    if crm:
        try:
            detail["lead"] = {
                "id": crm.get("id"),
                "org_name": crm.get("org_name"),
                "email": crm.get("email"),
                "phone": crm.get("phone"),
                "status": crm.get("status"),
                "score": crm.get("score"),
                "summary": crm.get("summary"),
                "tags": _json_list(crm.get("tags")),
                "obsidian_path": crm.get("obsidian_path"),
            }
        except Exception:
            detail["lead"] = None
    else:
        detail["lead"] = None

    # Laatste 3 mails van deze afzender (de thread).
    try:
        mails = conn.execute(
            "SELECT received_at, created_at, subject, body_text FROM mail_inbox "
            "WHERE lower(from_addr)=? ORDER BY COALESCE(received_at, created_at) DESC LIMIT 3",
            (from_addr,),
        ).fetchall()
        detail["recent_mails"] = [
            {
                "date": (m["received_at"] or m["created_at"] or "")[:16],
                "subject": (m["subject"] or "").strip(),
                "snippet": _snippet(m["body_text"] or "", 220),
            }
            for m in mails
        ]
    except Exception:
        detail["recent_mails"] = []

    return detail


def _goal_task_counts(conn, goal_id: str) -> Dict[str, int]:
    counts = {"total": 0}
    for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM goal_tasks WHERE goal_id=? GROUP BY status",
        (goal_id,),
    ):
        counts[r["status"]] = r["n"]
        counts["total"] += r["n"]
    return counts


def _campagne_auto_channels(conn, project: str, kanalen: List[str],
                            image_brief_json: str = "") -> List[str]:
    """Welke kanalen kan Impact OS voor dit pack ÉCHT automatisch plaatsen?

    Stuurt de zichtbaarheid van de 'Plaats op socials'-knop. Een kanaal komt
    alleen in deze lijst als de publish-chain hem daadwerkelijk kan doen —
    anders belooft de knop iets dat hij niet waar kan maken en liegt de UI.

    Regels (afgeleid van shared/social_content.publish_pack):
      - facebook : alleen als er een page-id + token is voor dit project.
      - twitter  : alleen als er API-creds zijn voor dit project.
      - instagram: alleen als geconfigureerd ÉN het pack een públieke image_url
                   heeft (IG weigert lokale files — anders manual-only).
      - linkedin : nooit automatisch — vanaf een persoonlijk profiel kan het per
                   definitie niet, dus altijd 'Ik heb 'm geplaatst'.
    """
    out: List[str] = []
    proj_norm = (project or "").strip()
    pub_img = ""
    try:
        ib = json.loads(image_brief_json or "{}")
        u = (ib.get("image_url") or "").strip()
        if u.startswith("http"):
            pub_img = u
    except Exception:
        pass
    for k in kanalen:
        k = k.strip().lower()
        try:
            if k == "facebook":
                from ...shared import facebook as fb
                if fb.is_configured(proj_norm):
                    out.append(k)
            elif k == "twitter":
                from ...shared import twitter as tw
                if tw.is_configured(proj_norm):
                    out.append(k)
            elif k == "instagram":
                from ...shared import instagram as ig
                # Configured én een publieke afbeelding beschikbaar — anders
                # valt publish_pack terug op manual en plaatst hij niets.
                if ig.is_configured(proj_norm) and pub_img:
                    out.append(k)
            elif k == "linkedin":
                pass  # bewust nooit automatisch
        except Exception:
            continue
    return out


def build_inbox(project: Optional[str] = None) -> Dict[str, Any]:
    """Verzamel alles wat op een menselijke beslissing wacht.

    Met `project` (vault-projectnaam, bv. "Bewaard voor Jou") worden alleen de
    items teruggegeven die bij dát project horen — inclusief de content-
    wachtrijen die onder een site-naam (bv. "DatingAssistent 40+") hangen. Zonder
    `project` blijft het de volledige inbox voor de Control Room.
    """
    # Canonnische sleutel van het gevraagde project, zodat we site- en
    # project-namen door elkaar heen kunnen matchen (zie _norm_project_key).
    target_key = _norm_project_key(project) if project else None
    items: List[Dict[str, Any]] = []
    # Lazy schema's: op een verse installatie zijn deze tabellen er pas na de
    # eerste aanroep van hun eigen domein — zonder dit crasht build_inbox op
    # "no such table" vóórdat iemand de Facturatie-tab ooit geopend heeft.
    from ..billing.models import ensure_schema as _ensure_billing_schema
    from ..crm.models import ensure_schema as _ensure_crm_schema
    _ensure_billing_schema()
    _ensure_crm_schema()
    with get_conn() as conn:
        skip = _dismissed(conn)

        # ── 0. Agenda-voorstellen (bovenaan: tijdgevoelig) ───────────────
        # Afspraak-voorstellen van de agenda-agent zijn tijdgevoelig en
        # wachten op een menselijke boek-beslissing. Daarom bóven de mail-
        # en fout-stromen: anders verdwijnen ze onder een volle inbox en
        # mis je een afspraak die eigenlijk "Plan in agenda" vraagt.
        try:
            from ...domains.calendar import agent as agenda_agent
            for p in agenda_agent.pending_proposals():
                conflict = (p.get("conflict_note") or "").strip()
                # ── Lead-status: kende we deze afzender al? ──────────────
                # Een afspraak-voorstel komt per mail binnen; de afzender kan een
                # bestaande klant/lead zijn of — vaker — een nieuw contact. Die
                # status hoort op de kaart zichtbaar te zijn vóórdat je boekt,
                # zodat je meteen ziet of dit een eerste contact is. De volledige
                # lead-status + recente mails worden in _calendar_detail() berekend.
                from_addr = (p.get("from_addr") or "").strip().lower()
                items.append({
                    "kind": "calendar_proposal",
                    "dismiss_kind": "calendar",
                    "id": p["id"],
                    "title": f"\U0001F4C5 Afspraak-voorstel: {p['subject'][:50] or p['from_addr']}",
                    "project": "Agenda",
                    "created_at": p.get("created_at"),
                    "summary": (
                        f"Voorgesteld: {p['proposed_start'][:16].replace('T', ' ')}–"
                        f"{p['proposed_end'][11:16]} · {p['priority']}"
                        + (f" · ⚠ {conflict[:120]}" if conflict else "")
                    ),
                    # Volledige detail-payload voor het "Detail bekijken"-paneel:
                    # lead-CRM, recente mails en lead-status in één call.
                    "detail": _calendar_detail(conn, p),
                    "actions": [
                        {"label": "Plan in agenda", "type": "calendar_approve", "id": p["id"]},
                        {"label": "Detail bekijken", "type": "calendar_detail", "id": p["id"]},
                        {"label": "Weiger", "type": "calendar_reject", "id": p["id"], "danger": True},
                    ],
                })
        except Exception:
            pass

        # Beleggingsvoorstellen staan bewust NIET in het Actiecentrum: die horen
        # uitsluitend op het Beursmeester-dashboard thuis (tabs-invest.js), niet
        # tussenin het algemene dashboard.

        # ── 1. Doelen die op jou wachten ────────────────────────────────
        for g in conn.execute(
            "SELECT id, title, status, project, created_at FROM goals "
            "WHERE status IN ('draft','ready','failed') ORDER BY created_at DESC"
        ):
            if ("goal", g["id"]) in skip:
                continue
            counts = _goal_task_counts(conn, g["id"])
            if g["status"] == "draft":
                summary = "Plan ligt klaar maar is nooit bevestigd — zonder jouw klik gebeurt er niets."
                actions = [
                    {"label": "Bevestig & start", "type": "goal_confirm_start", "id": g["id"]},
                    {"label": "Verwijder", "type": "goal_delete", "id": g["id"], "danger": True},
                ]
            elif g["status"] == "ready":
                summary = f"Bevestigd ({counts['total']} taken) — wacht op start."
                actions = [
                    {"label": "Start nu", "type": "goal_start", "id": g["id"]},
                    {"label": "Verwijder", "type": "goal_delete", "id": g["id"], "danger": True},
                ]
            else:  # failed
                summary = "Uitvoering is vastgelopen."
                actions = [
                    {"label": "Opnieuw proberen", "type": "goal_retry", "id": g["id"]},
                    {"label": "Verwijder", "type": "goal_delete", "id": g["id"], "danger": True},
                ]
            items.append({
                "kind": f"goal_{g['status']}",
                "dismiss_kind": "goal",
                "id": g["id"],
                "title": g["title"],
                "project": g["project"],
                "created_at": g["created_at"],
                "summary": summary,
                "actions": actions,
            })

        # ── 2. Wachtrij: content dat op review wacht ────────────────────
        # Harde regel: een artikel met score < CONTENT_MIN_SCORE mag NOOIT bij de
        # mens op het dashboard verschijnen — de agent moet het zelf verbeteren.
        # Jobs die desondanks in 'pending_review' met een te lage score staan
        # (oude data vóór de gate-fix, of een vastgelopen verbeter-loop) laten we
        # weg uit de inbox en rapporteren we als inconsistente-staat-logging, zodat
        # de content-verbeteraar (scheduler) ze oppakt i.p.v. de mens.
        from ...shared.config import content_min_score
        _seen_wachtrij_titles = set()
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.created_at, s.name AS site, "
            "COALESCE(j.content_type,'blog') AS content_type "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='pending_review' ORDER BY j.created_at DESC"
        ):
            if ("content", j["id"]) in skip:
                continue
            _dedup_key = ((j["site"] or "?"), _short_title(j["title"]).strip().lower())
            if _dedup_key in _seen_wachtrij_titles:
                # Zelfde site + zelfde titel al eerder in deze ronde getoond: een
                # tweede content_jobs-rij voor hetzelfde artikel (bijv. doordat de
                # keyword-dedupe in create_job niet aansloeg). Eén kaart, niet twee.
                continue
            _seen_wachtrij_titles.add(_dedup_key)
            score = int(j["seo_score"] or 0)
            _gate = content_min_score(j["site"])
            ct = (j["content_type"] or "blog").strip().lower()
            is_outreach = ct == "linkedin_outreach"
            is_hook = ct in ("hook", "snippet", "social_snippet")
            if score < _gate:
                # Inconsistent: onder grens maar wél in de goedkeuringsqueue.
                # Niet aan Vincent tonen — de agent lost het op (zie
                # content-pipeline improve-loop / scheduler verbeter-taak).
                import time as _time
                _now_ts = _time.monotonic()
                if _now_ts - _LOW_SCORE_WARNED.get(j["id"], -_LOW_SCORE_WARN_INTERVAL) \
                        >= _LOW_SCORE_WARN_INTERVAL:
                    _LOW_SCORE_WARNED[j["id"]] = _now_ts
                    logger.warning(
                        "[actiecentrum] Job %s (%s) staat op pending_review met score %s "
                        "< grens %s — weggelaten uit inbox, agent moet verbeteren. "
                        "(melding onderdrukt voor 1 uur)",
                        j["id"], j["title"], score, _gate,
                    )
                continue
            # Eerlijke subtekst + knoppen per content-type. Een hook/snippet is
            # GEEN pagina: de "Publiceer"-knop verdwijnt en maakt plaats voor
            # "Gebruik in artikel" (open de Wachtrij) + "Wijs af" — zo kan een
            # losse SEO-hook nooit per ongeluk als live pagina op de site.
            if is_outreach:
                summary = (
                    "LinkedIn-outreach klaar (SEO {seo}/100) — plak de berichten per "
                    "doelgroep op LinkedIn. Niet op de site publiceren."
                ).format(seo=j["seo_score"])
                actions = [
                    {"label": "Bekijk in Wachtrij", "type": "open_tab", "tab": "Wachtrij"},
                    {"label": "Klaar voor LinkedIn", "type": "content_ready_linkedin", "id": j["id"]},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ]
            elif is_hook:
                summary = (
                    "SEO-hook/snippet klaar (SEO {seo}/100) — géén artikel, wordt "
                    "NIET als pagina gepubliceerd. Gebruik het in een artikel of wijs af."
                ).format(seo=j["seo_score"])
                actions = [
                    {"label": "Bekijk in Wachtrij", "type": "open_tab", "tab": "Wachtrij"},
                    {"label": "Gebruik in artikel", "type": "open_tab", "tab": "Wachtrij"},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ]
            else:  # blog / article — echte pagina's
                summary = (
                    "Artikel klaar (SEO {seo}/100) — goedkeuren publiceert echt op de site."
                ).format(seo=j["seo_score"])
                actions = [
                    {"label": "Bekijk in Wachtrij", "type": "open_tab", "tab": "Wachtrij"},
                    {"label": "Publiceer", "type": "content_approve", "id": j["id"]},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ]
            items.append({
                "kind": "content_review",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": _short_title(j["title"]),
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "content_type": ct,
                "summary": summary,
                "actions": actions,
            })

        # ── 2a. Content onder de kwaliteitsgrens: verbeteren of afwijzen ─
        from ...shared.config import content_min_score
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.created_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='needs_work' ORDER BY j.created_at DESC"
        ):
            if ("content", j["id"]) in skip:
                continue
            items.append({
                "kind": "content_needs_work",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": j["title"],
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "summary": (
                    f"Score {j['seo_score']}/100 — onder de kwaliteitsgrens ({content_min_score(j['site'])}). "
                    "Publiceren is geblokkeerd; laat de agent herschrijven of wijs af."
                ),
                "actions": [
                    {"label": "Verbeter met AI", "type": "content_regenerate", "id": j["id"]},
                    {"label": "Handmatig aanpassen", "type": "content_manual_edit", "id": j["id"]},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ],
            })

        # ── 2a1. Vastgelopen content: de agent probeerde het en gaf het op ──
        # 'stuck' = content_improver + Orchestrator kwamen beide niet boven de
        # grens (max_attempts bereikt). Een vroegere versie van dit dashboard
        # droeg geen knop, waardoor Vincent handmatig moest zoeken. Nu: één
        # 'Reset & opnieuw' die de pogingentellers cleart en de job terugzet
        # naar 'needs_work' (zodat de content_improver en/of Orchestrator hem
        # opnieuw kunnen pakken). Zonder dat handmatige stappen in de shell.
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.improve_attempts, j.orchestrator_attempts, "
            "j.reviewed_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='stuck' ORDER BY j.created_at DESC"
        ):
            if ("content", j["id"]) in skip:
                continue
            attempts = (j["improve_attempts"] or 0) + (j["orchestrator_attempts"] or 0)
            items.append({
                "kind": "content_stuck",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": j["title"],
                "project": j["site"] or "?",
                "created_at": j["reviewed_at"] or j["created_at"],
                "summary": (
                    f"Score {j['seo_score']}/100 — {attempts}x geprobeerd, blijvend onder de grens. "
                    "De verbeteraar en de Orchestrator hebben hun pogingen opgebruikt. "
                    "Reset de tellers om het opnieuw te laten proberen, of wijs af."
                ),
                "actions": [
                    {"label": "Reset & opnieuw proberen", "type": "content_reset_stuck", "id": j["id"], "accent": True},
                    {"label": "Verbeter met AI", "type": "content_regenerate", "id": j["id"]},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ],
            })

        # ── 2b. Wachtrij-jobs waarvan publiceren misging: retry mogelijk ─
        # 'publish_failed' is wat approve_and_publish schrijft; 'error' is de
        # oudere naam en blijft meedoen voor bestaande rijen. Alleen op 'error'
        # filteren liet deze sectie permanent leeg: drie Daar-artikelen faalden
        # in juli 2026 (ontbrekende publish-credentials) en zijn nooit gemeld —
        # ze stonden zelfs op 'published' terwijl er niets online stond.
        for j in conn.execute(
            "SELECT j.id, j.title, j.error, j.publish_result, j.created_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status IN ('publish_failed','error') ORDER BY j.created_at DESC"
        ):
            if ("content", j["id"]) in skip:
                continue
            # Rijen van vóór de fix hebben een lege error-kolom: leid de oorzaak
            # dan alsnog af uit publish_result i.p.v. "Onbekende fout" te tonen.
            reden = j["error"] or ""
            if not reden:
                try:
                    reden = content_pipeline.publish_failure_reason(
                        json.loads(j["publish_result"] or "{}"))
                except (ValueError, TypeError):
                    reden = ""
            items.append({
                "kind": "error",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": f"Publiceren mislukt: {_short_title(j['title'])}",
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "summary": (reden or "Onbekende fout — zie publicatie-details")[:220],
                "actions": [
                    {"label": "Opnieuw publiceren", "type": "content_approve", "id": j["id"]},
                    {"label": "Analyseer & fix", "type": "error_triage", "id": j["id"],
                     "error_kind": "content_job", "accent": True},
                    {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "content", "id": j["id"]},
                ],
            })

        # ── 3. Conveyor-taken die op goedkeuring wachten ────────────────
        for t in conn.execute(
            "SELECT id, title, agent, created_at FROM tasks "
            "WHERE status='awaiting_approval' ORDER BY created_at DESC"
        ):
            if ("task", t["id"]) in skip:
                continue
            items.append({
                "kind": "task_approval",
                "dismiss_kind": "task",
                "id": t["id"],
                "title": t["title"],
                "project": t["agent"] or "Pipeline",
                "created_at": t["created_at"],
                "summary": "Taakresultaat wacht op jouw goedkeuring.",
                "actions": [
                    {"label": "Bekijk in Technisch", "type": "open_tab", "tab": "Technisch"},
                    {"label": "Keur goed", "type": "task_approve", "id": t["id"]},
                ],
            })

        # ── 4. Fouten die jouw actie vereisen (laatste 3 dagen) ─────────
        for e in conn.execute(
            "SELECT id, project, action, detail, created_at FROM activity_log "
            "WHERE (status='error' OR action LIKE '%fout%') "
            "AND created_at > datetime('now', ?) ORDER BY created_at DESC LIMIT 10",
            (f"-{ERROR_WINDOW_DAYS} day",),
        ):
            if ("error", e["id"]) in skip:
                continue
            # Zelfherstellend: een publicatiefout waarvoor later een geslaagde
            # 'live' van hetzelfde project+artikel bestaat, is opgelost — dat
            # is een logregel, geen actie-item.
            if e["action"] in ("live-fout", "publish-fout", "live-overgeslagen"):
                title_part = (e["detail"] or "").split("':")[0].lstrip("'")
                fixed = conn.execute(
                    "SELECT 1 FROM activity_log WHERE action='live' AND project=? "
                    "AND detail LIKE ? AND created_at >= ? LIMIT 1",
                    (e["project"], f"%{title_part[:60]}%", e["created_at"]),
                ).fetchone()
                if fixed:
                    continue
            # Generieke resolver (zelfde logica als Iris-metrics): fout waarvan
            # de job inmiddels published is, of waar een latere ok-regel met
            # dezelfde titel bestaat, is klaar — niet meer tonen.
            try:
                from ..iris import metrics as _iris_metrics
                if _iris_metrics._error_resolved(conn, dict(e)):
                    continue
            except Exception:
                pass
            actions: List[Dict[str, Any]] = [
                {"label": "Analyseer & fix", "type": "error_triage", "id": e["id"], "accent": True},
                {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "error", "id": e["id"]},
            ]
            # Een taak die nog nooit slaagde analyseer je niet weg — je draait
            # hem en leest de fout. De kaart droeg alleen "Analyseer & fix",
            # terwijl de stilstand-kaart ernaast wél de inhaalknop had; de
            # tekst zei "draai hem handmatig" en bood daar geen knop voor.
            if e["action"] == "job_nooit_geslaagd":
                job_id = (e["detail"] or "").split("|")[0].strip()
                if job_id:
                    actions.insert(0, {"label": "Nu draaien", "type": "run_job", "id": job_id})
            # 'afgekeurd maar live' droeg tot 18 aug 2026 alleen de instructie
            # "haal dit offline in het CMS" — geen knop, en dus ook nooit een
            # signaal dat het gebeurd was (zie confirm_depublished). Zoek de
            # afgewezen job terug op titel+project en bied de knop aan die
            # écht depubliceert en de kaart daarmee doet sluiten.
            if e["action"] == "afgekeurd_maar_live":
                m = re.search(r"'([^']{8,})'", e["detail"] or "")
                if m:
                    job_row = conn.execute(
                        "SELECT cj.id FROM content_jobs cj JOIN sites s ON s.id=cj.site_id "
                        "WHERE cj.title=? AND lower(s.name)=lower(?) AND cj.status='rejected' "
                        "ORDER BY cj.reviewed_at DESC LIMIT 1",
                        (m.group(1), e["project"] or ""),
                    ).fetchone()
                    if job_row:
                        actions.insert(0, {"label": "Haal offline", "type": "confirm_depublished",
                                            "id": job_row["id"], "accent": True})
            summary = (e["detail"] or "")[:220]
            # Werkt Iris hier al aan? Dan hoort de kaart dát te zeggen. Anders
            # kijkt Vincent naar een rood item terwijl er al iemand op zit — en
            # dat is precies hoe je leert dat rood niets betekent.
            try:
                from ..iris.selfheal import heal_status
                st = heal_status(e["action"] or "", e["detail"] or "", conn=conn)
            except Exception:
                st = None
            if st and st.get("last_result") in ("failed", "waiting"):
                summary = (
                    f"[Iris probeert dit zelf — poging {st['attempts']} van "
                    f"{st['max_attempts']}] {summary}"
                )
            items.append({
                "kind": "error",
                "dismiss_kind": "error",
                "id": e["id"],
                "title": _error_card_title(conn, dict(e)),
                "project": _display_project(conn, e["project"] or ""),
                "created_at": e["created_at"],
                "summary": summary,
                "self_heal": st,
                "actions": actions,
            })

        # ── 5. Kansen: vacatures met hoge fit (gegroepeerd) ─────────────
        vac = conn.execute(
            "SELECT COUNT(*) AS n, MAX(fit_score) AS top FROM vacancies "
            "WHERE status='new' AND fit_score >= ?",
            (VACANCY_FIT_THRESHOLD,),
        ).fetchone()
        if vac["n"] and ("vacancies", "open") not in skip:
            items.append({
                "kind": "vacancies",
                "dismiss_kind": "vacancies",
                "id": "open",
                "title": f"{vac['n']} interim-opdrachten met fit ≥ {VACANCY_FIT_THRESHOLD}",
                "project": "Opdrachten",
                "created_at": None,
                "summary": f"Beste match: fit {vac['top']}. Reageren op vacatures kan alleen jij.",
                "actions": [
                    {"label": "Open Opdrachten", "type": "open_tab", "tab": "Opdrachten"},
                    {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "vacancies", "id": "open"},
                ],
            })

        # ── 5b. Outreach-concepten die op jouw verzendklik wachten ──────
        # De input-kant van de acquisitieformule: de agent schreef het
        # concept, alleen jij kunt versturen (Wachtrij-gate voor e-mail).
        for l in conn.execute(
            "SELECT id, org_name, city, email, outreach_subject, outreach_draft, "
            "outreach_drafted_at, score FROM leads WHERE status='outreach_review' "
            "ORDER BY score DESC, outreach_drafted_at DESC"
        ):
            if ("outreach", l["id"]) in skip:
                continue
            preview = (l["outreach_draft"] or "").replace("\n", " ")[:140]
            items.append({
                "kind": "outreach_review",
                "dismiss_kind": "outreach",
                "id": l["id"],
                "title": f"Outreach klaar: {l['org_name']}" + (f" ({l['city']})" if l["city"] else ""),
                "project": "Leads",
                "created_at": l["outreach_drafted_at"] or None,
                "summary": f"‘{l['outreach_subject']}’ — {preview}",
                "actions": [
                    {"label": "Verstuur", "type": "outreach_send", "id": l["id"]},
                    {"label": "Wijs af (lead vervalt)", "type": "outreach_dismiss", "id": l["id"], "danger": True},
                ],
            })

        # ── 5c. Linkbuilding-concepten die op jouw verzendklik wachten ──
        # Zelfde gate als de acquisitie: de agent schreef het concept
        # (incl. concrete linksuggestie), alleen jij kunt versturen.
        for p in conn.execute(
            "SELECT id, domain, contact_email, outreach_subject, outreach_draft, "
            "outreach_drafted_at, relevance_score, target_url FROM link_prospects "
            "WHERE status='outreach_review' "
            "ORDER BY relevance_score DESC, outreach_drafted_at DESC"
        ):
            if ("linkbuilding", p["id"]) in skip:
                continue
            preview = (p["outreach_draft"] or "").replace("\n", " ")[:140]
            items.append({
                "kind": "linkbuilding_review",
                "dismiss_kind": "linkbuilding",
                "id": p["id"],
                "title": f"Link-outreach klaar: {p['domain']} (score {p['relevance_score']})",
                "project": "Linkbuilding",
                "created_at": p["outreach_drafted_at"] or None,
                "summary": f"‘{p['outreach_subject']}’ → link naar {p['target_url']} — {preview}",
                "actions": [
                    {"label": "Verstuur", "type": "linkbuilding_send", "id": p["id"]},
                    {"label": "Wijs af (kans vervalt)", "type": "linkbuilding_dismiss",
                     "id": p["id"], "danger": True},
                ],
            })

        # ── 5d. Facturatie: bonnetjes die niet konden worden doorgestuurd ──
        # Geen stille faalmodus (zie shared/failures.py-filosofie elders):
        # een bonnetje dat blijft steken op 'mislukt' moet zichtbaar zijn,
        # anders raakt een inkoopfactuur nooit in de boekhouding.
        mislukte_bonnetjes = conn.execute(
            "SELECT id, filename, forward_error, created_at FROM billing_receipts "
            "WHERE status = 'mislukt' ORDER BY created_at DESC"
        ).fetchall()
        if mislukte_bonnetjes and ("billing_receipts", "open") not in skip:
            items.append({
                "kind": "billing_receipt_failed",
                "dismiss_kind": "billing_receipts",
                "id": "open",
                "title": f"{len(mislukte_bonnetjes)} bonnetje(s) niet doorgestuurd naar DigiBoox",
                "project": "WeAreImpact",
                "created_at": mislukte_bonnetjes[0]["created_at"],
                "summary": mislukte_bonnetjes[0]["forward_error"] or "Onbekende fout",
                "actions": [
                    {"label": "Open Facturatie", "type": "open_tab", "tab": "Facturatie"},
                ],
            })

        # ── 5e. Facturatie: conceptfacturen die op controle wachten ────────
        # Agenda-uren zijn een aanname (geblokkeerd ≠ gewerkt) — dit item
        # herinnert eraan dat er een concept ligt, de controle zelf gebeurt
        # in de Facturatie-tab (regels uitsluiten kan alleen daar).
        for d in conn.execute(
            "SELECT id, client_name, period_start, period_end, created_at "
            "FROM billing_invoice_drafts WHERE status = 'concept' ORDER BY created_at DESC"
        ).fetchall():
            if ("billing_invoice", d["id"]) in skip:
                continue
            items.append({
                "kind": "billing_invoice_review",
                "dismiss_kind": "billing_invoice",
                "id": d["id"],
                "title": f"Conceptfactuur klaar: {d['client_name']}",
                "project": "WeAreImpact",
                "created_at": d["created_at"],
                "summary": f"Periode {d['period_start']} t/m {d['period_end']} — controleer de "
                           f"uren vóór goedkeuring.",
                "actions": [
                    {"label": "Open Facturatie", "type": "open_tab", "tab": "Facturatie"},
                ],
            })

        # ── 5f. Facturatie: herinneringen die op jouw verzendklik wachten ──
        for r in conn.execute(
            "SELECT id, client_name, tone, days_overdue, subject, draft, created_at "
            "FROM billing_reminders WHERE status = 'review' ORDER BY days_overdue DESC"
        ).fetchall():
            if ("billing_reminder", r["id"]) in skip:
                continue
            preview = (r["draft"] or "").replace("\n", " ")[:140]
            items.append({
                "kind": "billing_reminder_review",
                "dismiss_kind": "billing_reminder",
                "id": r["id"],
                "title": f"Herinnering ({r['tone']}) klaar: {r['client_name']} "
                         f"({r['days_overdue']} dagen te laat)",
                "project": "WeAreImpact",
                "created_at": r["created_at"],
                "summary": f"'{r['subject']}' — {preview}",
                "actions": [
                    {"label": "Verstuur", "type": "billing_reminder_send", "id": r["id"]},
                    {"label": "Sla over", "type": "billing_reminder_skip", "id": r["id"]},
                ],
            })

        # ── 5g. CRM: taken over de streefdatum ──────────────────────────
        overdue_tasks = conn.execute(
            "SELECT COUNT(*) AS n FROM crm_tasks WHERE status = 'open' AND due_date != '' "
            "AND due_date < date('now')"
        ).fetchone()
        if overdue_tasks["n"] and ("crm_tasks", "open") not in skip:
            items.append({
                "kind": "crm_tasks_overdue",
                "dismiss_kind": "crm_tasks",
                "id": "open",
                "title": f"{overdue_tasks['n']} CRM-taak/taken over de streefdatum",
                "project": "WeAreImpact",
                "created_at": None,
                "summary": "Follow-ups die blijven liggen kosten klanten, niet alleen tijd.",
                "actions": [
                    {"label": "Open Klanten", "type": "open_tab", "tab": "Klanten"},
                ],
            })

        # ── 6. Kansen: nieuwe leads (gegroepeerd) ───────────────────────
        leads = conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status='new'").fetchone()
        if leads["n"] and ("leads", "open") not in skip:
            items.append({
                "kind": "leads",
                "dismiss_kind": "leads",
                "id": "open",
                "title": f"{leads['n']} nieuwe leads wachten op eerste contact",
                "project": "Leads",
                "created_at": None,
                "summary": "Verrijken kan de agent; benaderen beslis jij.",
                "actions": [
                    {"label": "Open Leads", "type": "open_tab", "tab": "Leads"},
                    {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "leads", "id": "open"},
                ],
            })

        # ── 2c. Mail helpdesk: concept-antwoorden wachten op goedkeuring ──
        for r in conn.execute(
            "SELECT r.id, r.to_addr, r.subject, r.draft_body, r.created_at, "
            "m.project, m.address, i.from_name, i.received_at, i.from_addr, "
            "r.poot_referral, r.customer_status "
            "FROM mail_reply r "
            "JOIN mailboxes m ON m.id=r.mailbox_id "
            "JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.status='pending_review' ORDER BY r.created_at DESC"
        ):
            if ("mail", r["id"]) in skip:
                continue
            from_addr = (r["from_addr"] or "").strip().lower()
            known = bool(from_addr) and _is_known_sender(conn, from_addr)
            items.append({
                "kind": "mail_reply",
                "dismiss_kind": "mail",
                "id": r["id"],
                "title": f"Mail {r['from_name'] or r['to_addr']}: {r['subject']}",
                "project": r["project"] or "Helpdesk",
                # Binnenkomsttijd van de oorspronkelijke mail (i.received_at),
                # terugvallend op de concept-tijd — zodat het dashboard toont
                # wanneer het bericht écht binnenkwam, niet wanneer het concept
                # door de agent is geschreven.
                "created_at": r["received_at"] or r["created_at"],
                # Iris-regel: als er een Pootgelukkig-kans is gezien, geef dat
                # door als vlag zodat de UI 'm kan tonen (niet verplicht — Vincent
                # keurt het concept alsnog goed of laat de PS weg bij Bewerk).
                # 'Nieuwe afzender' tonen we alleen als de afzender écht onbekend
                # is: niet in het bekende-afzenders-register én niet met een
                # 'bekend' customer_status (die laatste is de historie-check van de
                # mail-agent zelf). Eén keer 'Markeer als bekend' klikken en de
                # afzender valt voortaan in deze groep.
                "flag": " · ".join(f for f in (
                    "Pootgelukkig-suggestie" if (r["poot_referral"] or "").strip() else None,
                    "Nieuwe afzender" if (r["customer_status"] != "bekend" and not known) else None,
                ) if f) or None,
                # Altijd meesturen zodat de UI weet of er een "Markeer als bekend"-
                # knop moet staan. Als de afzender al bekend is, laten we de knop
                # weg (je markeert geen bekende afzender nóg een keer).
                "sender_known": bool(known),
                "summary": (r["draft_body"][:240] + ("…" if len(r["draft_body"]) > 240 else "")),
                # GSC-expert-knop alleen bij échte Search Console-mails
                # (afzender sc-noreply@google.com of kenmerkende onderwerp/body).
                "actions": (
                    [
                        {"label": "Analyseer & fix (GSC)", "type": "mail_gsc_fix", "id": r["id"], "accent": True},
                    ]
                    if _is_gsc_mail(r["to_addr"], r["subject"], r["draft_body"])
                    else []
                ) + [
                    {"label": "Verstuur", "type": "mail_send", "id": r["id"]},
                    {"label": "Bewerk", "type": "mail_edit", "id": r["id"]},
                    {"label": "Afwijzen", "type": "mail_reject", "id": r["id"], "danger": True},
                    {"label": "Niet meer reageren", "type": "mail_ignore_sender",
                     "id": r["id"], "danger": True},
                ],
            })

        # ── 2d. Postvak: klaarstaande conceptantwoorden op eigen urgente mail ──
        # Ánders dan 2c (helpdesk-projectmailboxen): dit is Vincents eigen
        # postvak, en het concept is vooraf gegenereerd door
        # outlook.ensure_suggested_replies() (zie bridge/context.py build_mail)
        # omdat de bridge een pull-model is — er is geen 'tik en genereer nu'.
        for r in conn.execute(
            "SELECT id, subject, from_name, from_email, ai_summary, suggested_reply "
            "FROM outlook_emails "
            "WHERE folder='inbox' AND is_replied=0 AND suggested_reply_dismissed=0 "
            "AND suggested_reply IS NOT NULL AND suggested_reply != '' "
            "ORDER BY priority DESC, received_at DESC"
        ):
            if ("personal_mail", r["id"]) in skip:
                continue
            items.append({
                "kind": "personal_mail",
                "dismiss_kind": "personal_mail",
                "id": r["id"],
                "title": f"Mail {r['from_name'] or r['from_email']}: {r['subject']}",
                "project": "Postvak",
                "created_at": None,
                "summary": r["ai_summary"] or (r["suggested_reply"][:240]),
                # Bewerken en versturen zijn hier bewust één stap: het concept
                # staat editable in de detail-sheet, 'Verstuur' stuurt precies
                # de tekst die daar op dat moment staat (zie remote/app.js) —
                # geen apart 'Bewerk'-tussenstation zoals bij de helpdesk-flow.
                "actions": [
                    {"label": "Verstuur", "type": "personal_mail_send", "id": r["id"]},
                    {"label": "Afwijzen", "type": "personal_mail_reject", "id": r["id"], "danger": True},
                ],
            })

        # ── 2e. Social-inbox: concept-antwoorden op reacties/DM's ──────────
        # Zelfde patroon als 2d (Postvak) — bewust hier en niet alleen in de
        # bridge, want dismiss/skip loopt voor élk itemtype via dezelfde
        # inbox_dismissals-tabel en die skip-check zit alleen hier.
        for r in conn.execute(
            "SELECT m.id, m.platform, m.author_name, m.author_handle, m.text, "
            "m.draft_body, m.created_at, i.project "
            "FROM social_inbox_msg m JOIN social_inboxes i ON i.id=m.inbox_id "
            "WHERE m.status IN ('pending_review','edited') "
            "ORDER BY m.created_at DESC"
        ):
            if ("social", r["id"]) in skip:
                continue
            items.append({
                "kind": "social_msg",
                "dismiss_kind": "social",
                "id": r["id"],
                "title": f"{r['platform'].capitalize()} · {r['author_name'] or r['author_handle'] or 'iemand'}",
                "project": r["project"] or "Social",
                "created_at": r["created_at"],
                "summary": (r["draft_body"] or r["text"] or "")[:240],
                "actions": [
                    {"label": "Plaats antwoord", "type": "social_send", "id": r["id"]},
                    {"label": "Afwijzen", "type": "social_reject", "id": r["id"], "danger": True},
                ],
            })

        # ── 2f. Campagne: de post die vandaag (of eerder) had moeten staan ──
        # De invariant `campagnepost_over_datum` vangt dit óók, maar pas na een
        # dag speling plus drie dagen 'stil'-drempel — dat is de vangnet-melding
        # voor een campagne die stilvalt, niet de werkkaart voor vandaag. Een
        # plan van achttien posts heeft een kaart nódig op de ochtend zelf,
        # anders is de enige plek waar het slot staat opnieuw iemands geheugen.
        # Toekomstige posts blijven bewust weg: een inbox die volloopt met werk
        # van over drie weken is geen inbox meer. We tonen alleen de post die
        # VANDAAG gepland staat — verleden (te_laat) posts horen in de
        # `campagnepost_over_datum`-melding (zie hierboven), niet als werkkaart.
        vandaag_begin = date.today().isoformat() + "T00:00:00"
        vandaag_eind = date.today().isoformat() + "T23:59:59"
        try:
            campagne_rijen = conn.execute(
                "SELECT id, project, campaign, campaign_post, theme, angle, "
                "scheduled_for, copy_json, image_brief_json FROM social_posts "
                "WHERE campaign <> '' AND scheduled_for <> '' "
                "AND scheduled_for >= ? AND scheduled_for <= ? "
                "AND status = 'pending_review' ORDER BY scheduled_for",
                (vandaag_begin, vandaag_eind),
            ).fetchall()
        except sqlite3.OperationalError:
            campagne_rijen = []
        for r in campagne_rijen:
            if ("campagne_post", r["id"]) in skip:
                continue
            kanalen = sorted(_json_dict(r["copy_json"]).keys())
            gepland = (r["scheduled_for"] or "")
            te_laat = gepland[:10] < date.today().isoformat()
            # Welke kanalen kan Impact OS ÉCHT automatisch plaatsen voor dit pack?
            # Alleen die krijgen de groene "Plaats op socials"-knop — de rest
            # kan toch niet (geen token / geen publieke image / LinkedIn is per
            # definitie handmatig). Zo liegt de UI nooit dat ze geplaatst wordt.
            auto_kanalen = _campagne_auto_channels(conn, r["project"], kanalen, r["image_brief_json"])
            actions = [
                {"label": "Bekijk & kopieer", "type": "open_tab", "tab": "Social Creatie"},
            ]
            if auto_kanalen:
                actions.append({
                    "label": "Plaats op socials", "type": "campagne_publish",
                    "id": r["id"], "channels": auto_kanalen,
                })
            actions.append({
                "label": "Ik heb 'm geplaatst", "type": "campagne_posted", "id": r["id"],
            })
            actions.append({
                "label": "Sla over", "type": "campagne_skip", "id": r["id"], "danger": True,
            })
            items.append({
                "kind": "campagne_post",
                "dismiss_kind": "campagne_post",
                "id": r["id"],
                "title": f"Campagnepost {r['campaign_post']}: {_short_title(r['theme'])}",
                "project": _display_project(conn, r["project"]),
                "created_at": gepland,
                "summary": (
                    ("STOND GEPLAND VOOR " + gepland[:10] + " — " if te_laat else "")
                    + f"Klaar voor {', '.join(kanalen) or 'geen kanaal'}. "
                    + (f"Automatisch plaatsbaar op: {', '.join(auto_kanalen)}. " if auto_kanalen
                       else "Geen kanaal automatisch beschikbaar — plaats handmatig. ")
                    + (r["angle"] or "")[:240]
                ),
                "actions": actions,
            })

    # Stilstand: geplande runs die overgingen terwijl de machine uit stond.
    # Eén item per taak, niet per gemist vuurmoment — vier kaarten voor vier
    # dagen dezelfde stilstand zeggen niets extra's en verdringen wel vier
    # andere dingen. De knop draait de taak alsnog; zonder die knop is de
    # melding alleen een mededeling dat er werk verdampt is.
    try:
        from ...shared import downtime
        for gap in downtime.summary():
            if not gap["recoverable"]:
                continue
            # De wegklik-sleutel bevat het laatste gemiste moment, niet alleen
            # het job-id. Anders zou één keer "gezien, verberg" deze taak voor
            # altijd onzichtbaar maken — óók de stilstand van volgende maand.
            dismiss_ref = f"{gap['job_id']}:{gap['last']}"
            if ("scheduler_gap", dismiss_ref) in skip:
                continue
            items.append({
                "kind": "error",
                "dismiss_kind": "scheduler_gap",
                "id": gap["job_id"],
                "title": f"Taak niet gedraaid: {gap['label']}",
                "project": "Scheduler",
                "created_at": gap["last"],
                "summary": gap["detail"],
                "actions": [
                    {"label": "Nu alsnog draaien", "type": "run_job", "id": gap["job_id"]},
                    {"label": "Gezien, verberg", "type": "dismiss",
                     "dismiss_kind": "scheduler_gap", "id": dismiss_ref},
                ],
            })
    except Exception:
        logger.exception("[actiecentrum] Kon gemiste runs niet ophalen")

    # Scheduler-fouten. Staan sinds de run-historie in `scheduler_runs` ook een
    # herstart door: een gefaalde job blijft in het Actiecentrum tot hij slaagt.
    try:
        from ...scheduler import get_scheduler_status
        for job in get_scheduler_status().get("jobs", []):
            last = job.get("last_run")
            if not last:
                continue
            status = last.get("status")
            if status == "error":
                items.append({
                    "kind": "error",
                    "dismiss_kind": "scheduler",
                    "id": job["id"],
                    "title": f"Geplande taak faalde: {job['label']}",
                    "project": "Scheduler",
                    "created_at": last.get("time"),
                    "summary": (last.get("error") or "")[:220],
                    "actions": [
                        {"label": "Bekijk in Technisch", "type": "open_tab", "tab": "Technisch"},
                    ],
                })
            elif status == "action_required":
                # Integratie ontbreekt (bv. Outlook/Microsoft niet gekoppeld).
                # Een mens moet iets doen — toon dat als 'actie van jou nodig',
                # niet als een harde fout. Geen tracestack, één heldere kaart.
                items.append({
                    "kind": "attention",
                    "dismiss_kind": "scheduler",
                    "id": job["id"],
                    "title": f"Actie vereist: {job['label']}",
                    "project": "Scheduler",
                    "created_at": last.get("time"),
                    "summary": (last.get("error") or "")[:220],
                    "actions": [
                        {"label": "Koppel in Instellingen", "type": "open_tab", "tab": "Instellingen"},
                    ],
                })
    except Exception:
        pass

    errors = [i for i in items if i["kind"] == "error"]
    # ── Project-scope: bij een gevraagd project houden we alleen de items die
    # er écht bij horen. De check gebeurt vóórdat de tellingen worden gebouwd,
    # zodat ook de counts (total/needs_you/errors) over het gefilterde lijstje
    # gaan — de projectview toont dan exact "wat wacht er op mij" voor dát project.
    if target_key:
        items = [i for i in items if _item_belongs_to_project(i.get("project"), target_key)]
        errors = [i for i in items if i["kind"] == "error"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "counts": {
            "total": len(items),
            "needs_you": len(items) - len(errors),
            "errors": len(errors),
        },
        "items": items,
    }


def inbox_counts_by_project() -> Dict[str, int]:
    """Telling per project van alle open actie-items.

    Hertgebruikt dezelfde project-resolutie als build_inbox() (incl.
    site→project-normalisatie) en groepeert op de leesbare project-naam,
    zodat de Control-Room-kaken een badge krijgen met exact hetzelfde getal
    als de bijbehorende projectview. Cross-cutting items (Agenda, Leads,
    Scheduler, …) hebben geen project en worden niet meegeteld.
    """
    inbox = build_inbox()
    counts: Dict[str, int] = {}
    for it in inbox.get("items", []):
        key = _resolve_item_project(it.get("project"))
        if not key:
            continue
        # Map de genormaliseerde sleutel terug naar een leesbare naam via de
        # gecachte project-index (known_keys bevat de genormaliseerde vormen,
        # niet de originele namen — dus we herleiden de naam uit goals/vault).
        name = _project_display_name(key) or key
        counts[name] = counts.get(name, 0) + 1
    return counts


def _project_display_name(key: str) -> Optional[str]:
    """Genormaliseerde project-sleutel → leesbare naam (vault-projectnaam)."""
    global _PROJECT_INDEX
    if _PROJECT_INDEX is None:
        _PROJECT_INDEX = _build_project_index()
    known, _site_to_proj, _goal_to_proj = _PROJECT_INDEX
    # known bevat genormaliseerde namen; de originele naam halen we uit de
    # vault/sites. Simpelste: doorzoek goals + sites op de genormaliseerde match.
    with get_conn() as conn:
        for row in conn.execute("SELECT DISTINCT project FROM goals"):
            p = row["project"]
            if p and p not in ("all", "Globaal") and _norm_project_key(p) == key:
                return p
        for row in conn.execute("SELECT DISTINCT name FROM sites"):
            s = row["name"] or ""
            if _norm_project_key(s) == key:
                return s
    return None


def dismiss(kind: str, ref_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO inbox_dismissals (kind, ref_id, dismissed_at) "
            "VALUES (?, ?, datetime('now'))",
            (kind, ref_id),
        )


def outcome_feed(limit: int = 25) -> List[Dict[str, Any]]:
    """Recente uitkomst-kaarten: wat gedaan → waar → wat nu."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project, action, detail, artifact, next_step, status, created_at "
            "FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
