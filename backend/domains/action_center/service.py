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
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...shared.database import get_conn
from ..mail.gsc_expert import is_gsc_mail as _is_gsc_mail
from ..publish import content_pipeline


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


def _goal_task_counts(conn, goal_id: str) -> Dict[str, int]:
    counts = {"total": 0}
    for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM goal_tasks WHERE goal_id=? GROUP BY status",
        (goal_id,),
    ):
        counts[r["status"]] = r["n"]
        counts["total"] += r["n"]
    return counts


def build_inbox() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
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
                items.append({
                    "kind": "calendar_proposal",
                    "dismiss_kind": "calendar",
                    "id": p["id"],
                    "title": f"\U0001F4C5 Afspraak-voorstel: {p['subject'][:50] or p['from_addr']}",
                    "project": "Agenda",
                    "created_at": p.get("created_at"),
                    "summary": (
                        f"Voorgesteld: {p['proposed_start'][:16].replace('T', ' ')}\u2013"
                        f"{p['proposed_end'][11:16]} \u00b7 {p['priority']}"
                        + (f" \u00b7 \u26A0 {conflict[:120]}" if conflict else "")
                    ),
                    "actions": [
                        {"label": "Plan in agenda", "type": "calendar_approve", "id": p["id"]},
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
        from ...shared.config import CONTENT_MIN_SCORE
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.created_at, s.name AS site, "
            "COALESCE(j.content_type,'blog') AS content_type "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='pending_review' ORDER BY j.created_at DESC"
        ):
            score = int(j["seo_score"] or 0)
            ct = (j["content_type"] or "blog").strip().lower()
            is_outreach = ct == "linkedin_outreach"
            is_hook = ct in ("hook", "snippet", "social_snippet")
            if score < CONTENT_MIN_SCORE:
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
                        j["id"], j["title"], score, CONTENT_MIN_SCORE,
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
                "title": j["title"],
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "summary": summary,
                "actions": actions,
            })

        # ── 2a. Content onder de kwaliteitsgrens: verbeteren of afwijzen ─
        from ...shared.config import CONTENT_MIN_SCORE
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
                    f"Score {j['seo_score']}/100 — onder de kwaliteitsgrens ({CONTENT_MIN_SCORE}). "
                    "Publiceren is geblokkeerd; laat de agent herschrijven of wijs af."
                ),
                "actions": [
                    {"label": "Verbeter met AI", "type": "content_regenerate", "id": j["id"]},
                    {"label": "Handmatig aanpassen", "type": "content_manual_edit", "id": j["id"]},
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
            "m.project, m.address, i.from_name "
            "FROM mail_reply r "
            "JOIN mailboxes m ON m.id=r.mailbox_id "
            "JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.status='pending_review' ORDER BY r.created_at DESC"
        ):
            if ("mail", r["id"]) in skip:
                continue
            items.append({
                "kind": "mail_reply",
                "dismiss_kind": "mail",
                "id": r["id"],
                "title": f"Mail {r['from_name'] or r['to_addr']}: {r['subject']}",
                "project": r["project"] or "Helpdesk",
                "created_at": r["created_at"],
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
            if last and last.get("status") == "error":
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
    except Exception:
        pass

    errors = [i for i in items if i["kind"] == "error"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "total": len(items),
            "needs_you": len(items) - len(errors),
            "errors": len(errors),
        },
        "items": items,
    }


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
