"""
Bridge-acties — de whitelist van besluiten die de cloud-companion mag laten
uitvoeren. Elk besluit loopt door exact dezelfde servicefuncties als de knoppen
in de lokale UI; de cloud kan dus nooit een gate omzeilen of een willekeurig
endpoint aanroepen. Onbekende (kind, action)-combinaties worden hard geweigerd.

Een decision uit Neon ziet er zo uit:
    {"id": 7, "item_kind": "mail", "item_id": "12", "action": "send",
     "payload": {...}}

Retour: (ok: bool, message: str) — gaat terug naar de cloud zodat de telefoon
toont wat er met het besluit gebeurde ("verstuurd" / "geweigerd: ...").
"""
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


async def _content_approve(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..publish import content_pipeline
    # Een besluit van onderweg kan de lokale machine inhalen: Vincent keurt
    # op zijn telefoon iets goed dat hij (of een andere sync) intussen al
    # lokaal publiceerde. De job staat dan al op 'published' — dat is het
    # bedoelde eindresultaat, geen fout. Zonder deze check gooit
    # approve_and_publish een ValueError ('niet pending_review/publish_failed')
    # en meldt de Bridge dat elke keer als 'remote_decision_failed', terwijl er
    # niets stuk is.
    job = content_pipeline.get_job(item_id)
    if job and job.get("status") == "published":
        return True, "Was al gepubliceerd (geen actie nodig)"
    # Opt-in, net als in de UI: zonder expliciete channels-lijst alleen website.
    channels = []
    if payload.get("social") is not False and "channels" in payload:
        channels = [str(c).strip().lower() for c in (payload.get("channels") or [])]
    result = await content_pipeline.approve_and_publish(item_id, social_channels=channels)
    return True, f"Gepubliceerd: {str(result)[:200]}"


async def _content_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..publish import content_pipeline
    content_pipeline.reject_job(item_id)
    return True, "Afgewezen"


async def _mail_send(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..mail import service as mail
    ok = mail.send_reply(int(item_id))
    return (True, "Verstuurd") if ok else (False, "Versturen mislukt (zie logboek)")


async def _mail_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..mail import service as mail
    mail.reject_reply(int(item_id))
    return True, "Afgewezen"


async def _mail_edit(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..mail import service as mail
    text = (payload.get("text") or "").strip()
    if not text:
        return False, "Lege tekst — bewerking genegeerd"
    mail.edit_reply(int(item_id), text)
    return True, "Bewerking opgeslagen (blijft ter review staan)"


async def _personal_mail_send(item_id: str, payload: Dict) -> Tuple[bool, str]:
    """Verstuurt het (evt. bewerkte) concept via Vincents eigen postvak.

    Ánders dan _mail_send (helpdesk, projectmailboxen): dit gaat via Graph als
    Vincent zelf (send_reply, Mail.Send-scope), niet via een projectmailbox."""
    from ..outlook import service as outlook
    body = (payload.get("text") or "").strip()
    if not body:
        return False, "Geen concepttekst — versturen geweigerd"
    try:
        result = await outlook.send_reply(item_id, outlook.text_to_html(body))
    except Exception as e:  # noqa: BLE001
        return False, f"Versturen mislukt: {str(e)[:250]}"
    return (True, "Verstuurd") if result.get("success") else (
        False, result.get("error") or "Versturen mislukt")


async def _personal_mail_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..outlook import service as outlook
    outlook.dismiss_suggested_reply(item_id)
    return True, "Concept afgewezen"


async def _outreach_approve(item_id: str, payload: Dict) -> Tuple[bool, str]:
    # Bewust via de routerfunctie: die bevat de volledige verzendketen
    # (adres-validatie, Outlook-check, funnel-tijdstempel, uitkomst-kaart).
    from fastapi import HTTPException
    from ..prospecting.router import OutreachApproveRequest, approve_outreach
    try:
        result = await approve_outreach(item_id, OutreachApproveRequest(
            subject=payload.get("subject") or "", body=payload.get("body") or ""))
        return True, f"Verstuurd aan {result.get('to', '?')}"
    except HTTPException as e:
        return False, str(e.detail)[:300]


async def _outreach_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..prospecting.router import dismiss_outreach
    dismiss_outreach(item_id)
    return True, "Lead afgewezen (→ lost)"


async def _calendar_approve(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..calendar import agent as calendar_agent
    result = calendar_agent.approve_proposal(int(item_id))
    if result.get("ok"):
        return True, f"Geboekt: {result.get('link') or result.get('event_id') or 'ok'}"
    return False, result.get("error") or "Boeken mislukt"


async def _calendar_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..calendar import agent as calendar_agent
    calendar_agent.reject_proposal(int(item_id))
    return True, "Voorstel afgewezen"


async def _dismiss(kind: str, item_id: str) -> Tuple[bool, str]:
    from ..action_center import service as ac
    ac.dismiss(kind, item_id)
    return True, "Weggeklikt"


# ── Commando's: werk aanzwengelen vanaf de telefoon ─────────────────────────
#
# Tot nu toe was de bridge puur reactief: je kon afhandelen wat de machine al
# had klaargezet, maar niets in gang zetten. Dat is het verschil tussen een
# afstandsbediening en een assistent. Deze commando's starten agents — en zijn
# veilig om exact dezelfde reden als Iris' eigen hendels: het resultaat landt
# ALTIJD in een review-gate (Wachtrij / outreach_review / voorstel), nooit
# extern. Ze hergebruiken bewust `iris/actions.py`, inclusief de klemmen en de
# dedupe van max één run per dag per doelwit — anders kan een telefoon in een
# broekzak de LLM-rekening leegtrekken.

async def _cmd_content_run(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    site = str(payload.get("site") or "").strip()
    if not site:
        return False, "Geef een site op (bv. 'weareimpact')"
    result = await iris_actions.content_run(site, payload.get("count") or 1,
                                            "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, f"Contentmotor voor '{site}' mislukt of site onbekend")


async def _cmd_outreach_run(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    result = await iris_actions.outreach_run(payload.get("count") or 5,
                                             "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, "Outreach-batch mislukt (zie logboek)")


async def _cmd_seo_refresh(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    site = str(payload.get("site") or "").strip()
    if not site:
        return False, "Geef een site op"
    result = await iris_actions.seo_refresh(site, payload.get("count") or 1,
                                            "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, f"SEO-refresh voor '{site}' mislukt")


async def _cmd_lead_search(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    result = await iris_actions.lead_search_run(
        payload.get("queries") or [], "Aangevraagd via Iris Remote",
        template=str(payload.get("template") or ""))
    return (True, result) if result else (False, "Lead-zoekactie mislukt (zie logboek)")


async def _cmd_linkbuilding_run(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    result = await iris_actions.linkbuilding_run(payload.get("count") or 5,
                                                 "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, "Linkbuilding-batch mislukt (zie logboek)")


async def _cmd_mail_sync(payload: Dict) -> Tuple[bool, str]:
    """Postvak ophalen + triëren. Leest en beoordeelt; verstuurt niets."""
    from ..outlook import service as outlook
    if not outlook.is_authenticated():
        return False, "Outlook niet ingelogd — log opnieuw in via de Mail-tab"
    mails = await outlook.sync_inbox(limit=50)
    triaged = 0
    if payload.get("triage") is not False:
        async for event in outlook.batch_triage(limit=15):
            if event.get("type") == "batch_done":
                triaged = event.get("total", 0)
    return True, f"{len(mails)} mail(s) opgehaald, {triaged} getrieerd"


async def _cmd_mail_rule(payload: Dict) -> Tuple[bool, str]:
    """"Nooit meer van deze afzender", getikt op je telefoon.

    Dit is de enige weg: de mailregels in het Postvak-scherm bestaan niet als
    `sync_items`, dus kunnen ze geen gewoon besluit (`decide`) dragen — alleen
    mail mét conceptantwoord is een item. Het commando raakt bewust niets in de
    échte mailbox: er wordt niets verplaatst en niets verwijderd, alleen bepaald
    wat jóu nog bereikt. En het is omkeerbaar (Geblokkeerde afzenders → intrekken).
    """
    from ..outlook import service as outlook
    from ..outlook import rules

    scope = str(payload.get("scope") or "adres")
    actie = str(payload.get("action") or "spam")
    email_id = str(payload.get("email_id") or "").strip()
    adres = str(payload.get("email") or "").strip()

    try:
        if email_id:
            uitslag = outlook.block_sender(email_id, scope=scope, action=actie)
            patroon, geraakt = uitslag["pattern"], uitslag["applied"]
        elif adres:
            rule = rules.add_rule(adres, scope=scope, action=actie, source="mens")
            patroon, geraakt = rule["pattern"], rule.get("applied", 0)
        else:
            return False, "Geen afzender meegegeven"
    except ValueError as e:
        return False, str(e)[:200]

    if actie == rules.ACTIE_ALTIJD_TONEN:
        return True, f"'{patroon}' blijft voortaan zichtbaar ({geraakt} teruggezet)"
    return True, f"'{patroon}' geblokkeerd — {geraakt} mail(s) opgeruimd"


async def _cmd_mail_archive(payload: Dict) -> Tuple[bool, str]:
    """Deze mail hoeft niets van je. Géén regel — de afzender blijft welkom."""
    from ..outlook import service as outlook
    email_id = str(payload.get("email_id") or "").strip()
    if not email_id:
        return False, "Geen mail meegegeven"
    try:
        outlook.archive_email(email_id)
    except ValueError as e:
        return False, str(e)[:200]
    return True, "Gearchiveerd"


async def _cmd_helpdesk_run(payload: Dict) -> Tuple[bool, str]:
    """Helpdesk-mailboxen langsgaan: concepten schrijven, niets versturen."""
    from ..mail import service as mail
    import asyncio
    result = await asyncio.to_thread(mail.run_all_mailboxes)
    total = sum(v for v in result.values() if isinstance(v, int))
    return True, f"{total} nieuw(e) concept(en) klaargezet ter review"


async def _cmd_iris_briefing(payload: Dict) -> Tuple[bool, str]:
    from ..iris import service as iris
    report = await iris.run_morning_briefing()
    if not report:
        return False, "Briefing leverde niets op (zie logboek)"
    return True, f"Briefing van {report.get('report_date') or 'vandaag'} klaar"


async def _cmd_context_refresh(payload: Dict) -> Tuple[bool, str]:
    """Gooi de contextcache leeg zodat de eerstvolgende sync verse cijfers
    ophaalt — voor als je onderweg niet wilt wachten op de TTL."""
    from ...shared.database import get_conn
    keys = payload.get("sections") or ["mail", "agenda", "analytics", "seo"]
    with get_conn() as conn:
        for key in keys:
            conn.execute("DELETE FROM bridge_context_cache WHERE key = ?", (str(key),))
    return True, f"Context ververst bij de volgende sync ({', '.join(map(str, keys))})"


async def _cmd_ritual_morning_save(payload: Dict) -> Tuple[bool, str]:
    """Ochtendritueel loggen vanaf de telefoon — intentie, energie, dankbaarheid.

    Merge-not-overwrite: `save_morning` is een upsert op datum die élk veld
    vervangt (ook de velden die de telefoon niet meestuurt, zoals de
    focusblokken die je 's ochtends achter je bureau al invulde). Een quick-log
    onderweg mag dat niet wegvegen — dus vult dit eerst de bestaande rij aan
    met wat er al lag, vóór het opslaat.
    """
    from ..rituals import service as rituals
    svc = rituals.get_service()
    date = str(payload.get("date") or "").strip() or rituals._today()
    existing = svc.get_morning(date) or {}
    data = {
        "intentie": payload.get("intentie", existing.get("intentie", "")),
        "affirmatie": payload.get("affirmatie", existing.get("affirmatie", "")),
        "dankbaarheid": payload.get("dankbaarheid", existing.get("dankbaarheid", [])),
        "energyLevel": payload.get("energyLevel", existing.get("energy_level", 7)),
        "sleepQuality": payload.get("sleepQuality", existing.get("sleep_quality", 7)),
        "sleepTime": payload.get("sleepTime", existing.get("sleep_time", "")),
        "wakeTime": payload.get("wakeTime", existing.get("wake_time", "")),
        "focusBlok1": payload.get("focusBlok1", existing.get("focus_blok1", {})),
        "focusBlok2": payload.get("focusBlok2", existing.get("focus_blok2", {})),
    }
    svc.save_morning(date, data)
    return True, f"Ochtendritueel vastgelegd voor {date}"


async def _cmd_ritual_evening_save(payload: Dict) -> Tuple[bool, str]:
    """Avondritueel loggen vanaf de telefoon. Zelfde merge-regel als de ochtend."""
    from ..rituals import service as rituals
    svc = rituals.get_service()
    date = str(payload.get("date") or "").strip() or rituals._today()
    existing = svc.get_evening(date) or {}
    data = {
        "whatWentWell": payload.get("whatWentWell", existing.get("what_went_well", "")),
        "biggestWin": payload.get("biggestWin", existing.get("biggest_win", "")),
        "whatLearned": payload.get("whatLearned", existing.get("what_learned", "")),
        "challenges": payload.get("challenges", existing.get("challenges", "")),
        "energyLevel": payload.get("energyLevel", existing.get("energy_level", 5)),
        "tomorrowTop3": payload.get("tomorrowTop3", existing.get("tomorrow_top3", [])),
        "gratitude": payload.get("gratitude", existing.get("gratitude", "")),
        "adhdScores": payload.get("adhdScores", existing.get("adhd_scores", {})),
    }
    svc.save_evening(date, data)
    return True, f"Avondritueel vastgelegd voor {date}"


async def _cmd_ritual_win_add(payload: Dict) -> Tuple[bool, str]:
    from ..rituals import service as rituals
    title = str(payload.get("title") or "").strip()
    if not title:
        return False, "Geen titel meegegeven"
    rituals.get_service().add_win({
        "title": title,
        "description": payload.get("description", ""),
        "category": payload.get("category", "personal"),
        "impactLevel": payload.get("impactLevel", 3),
    })
    return True, f"Win vastgelegd: {title}"


async def _cmd_ritual_goal_progress(payload: Dict) -> Tuple[bool, str]:
    from ..rituals import service as rituals
    gid = payload.get("goal_id")
    if gid is None:
        return False, "Geen doel meegegeven"
    patch: Dict[str, Any] = {}
    if "progress" in payload:
        patch["progress"] = payload["progress"]
    if "completed" in payload:
        patch["completed"] = payload["completed"]
    if not patch:
        return False, "Niets om bij te werken"
    result = rituals.get_service().update_goal(int(gid), patch)
    if not result:
        return False, f"Doel #{gid} niet gevonden"
    return True, f"Voortgang bijgewerkt: '{result['title']}' → {result['progress']}%"


async def _cmd_orchestrator_run(payload: Dict) -> Tuple[bool, str]:
    """Zet één 'stuck'/'rejected' content-stuk op de zware Gauntlet Loop.

    Bewust géén site/count-parameter: de Orchestrator kiest zelf het eerste
    stuk uit `orchestrator.service._find_under_threshold_jobs()` (dezelfde
    lijst die de telefoon ziet via de context-snapshot). Kan enkele minuten
    duren — de bridge-sync-cyclus wacht dat gewoon af, net als content_run.
    Staat hier op de commando-whitelist zodat Vincent 'm kan aftikken, maar
    NIET in remote/api/iris.js' COMMANDS: cloud-Iris mag 'm niet zelf starten
    (zie orchestrator/service.py — dit is een kostbare, oordeel-vereisende
    escalatie, geen goedkope routine-actie zoals content_run/seo_refresh).
    """
    from ..orchestrator import service as orchestrator_service
    result = await orchestrator_service.process_one_under_threshold()
    if result.get("processed"):
        return True, f"Herschreven en teruggezet in de Wachtrij (job {result.get('published_job_id')})"
    reason = result.get("reason") or "niets verwerkt"
    if reason == "geen stukken onder de grens":
        return True, "Niets vastgelopen — geen stuck/rejected stukken onder de grens"
    return False, reason


async def _cmd_digest(payload: Dict) -> Tuple[bool, str]:
    from ..action_center import digest
    await digest.run_daily_digest()
    return True, "Ochtendrapport gedraaid (gemaild als SMTP is ingesteld)"


# ── Iris-onboarding vanaf Iris Remote (wizard verhuisd, zie remote/app.js) ──
# Stap 1/2/4 zijn kale dataschrijvingen — dezelfde servicefuncties als de
# oude lokale wizard riep, nu alleen via het commando-pad in plaats van een
# directe HTTP-call vanaf de telefoon (die er nooit was, want Iris Remote kan
# geen `localhost:1250` bereiken). Stap 3 (OAuth) loopt niet via een commando
# dat de telefoon indient, maar via `oauth_token_relay` hieronder — dat
# schrijft ZELF een decision, aangemaakt door remote/api/oauth.js ná de
# consent-redirect, niet door een tik op de telefoon.

async def _cmd_onboarding_step1(payload: Dict) -> Tuple[bool, str]:
    from ..onboarding import service as onboarding
    site_id = str(payload.get("site_id") or "").strip()
    if not site_id:
        return False, "Geen site_id meegegeven"
    onboarding.save_step1(site_id, str(payload.get("profile") or ""))
    return True, "Bedrijfsdoel opgeslagen"


async def _cmd_onboarding_step2(payload: Dict) -> Tuple[bool, str]:
    from ..onboarding import service as onboarding
    site_id = str(payload.get("site_id") or "").strip()
    if not site_id:
        return False, "Geen site_id meegegeven"
    await onboarding.save_step2(site_id, str(payload.get("tone_text") or ""))
    return True, "Schrijfstijl opgeslagen"


async def _cmd_onboarding_step4(payload: Dict) -> Tuple[bool, str]:
    from ..onboarding import service as onboarding
    site_id = str(payload.get("site_id") or "").strip()
    if not site_id:
        return False, "Geen site_id meegegeven"
    preset = str(payload.get("preset") or "").strip()
    overrides = payload.get("overrides") or None
    onboarding.save_step4(site_id, preset, overrides)
    return True, "Werk-grenzen opgeslagen"


async def _cmd_onboarding_complete(payload: Dict) -> Tuple[bool, str]:
    from ..onboarding import service as onboarding
    site_id = str(payload.get("site_id") or "").strip()
    if not site_id:
        return False, "Geen site_id meegegeven"
    status = onboarding.complete_onboarding(site_id)
    return True, f"Onboarding afgerond voor {status.get('project', site_id)}"


async def _cmd_onboarding_new_client(payload: Dict) -> Tuple[bool, str]:
    """"+ Nieuwe klant" vanaf de telefoon: maakt de site aan zodat de wizard
    daarna stap 1 kan indienen. Geen review-gate nodig — dit maakt alleen een
    lege rij aan, precies zoals de oude lokale 'startNewClientOnboarding()'."""
    from ..seo import sites as sites_service
    name = str(payload.get("name") or "").strip()
    if not name:
        return False, "Geen naam meegegeven"
    site = sites_service.create_site({"name": name})
    return True, f"Nieuwe klant aangemaakt: {site['name']} (site_id={site['id']})"


async def _cmd_oauth_token_relay(payload: Dict) -> Tuple[bool, str]:
    """Komt NIET van een tik op de telefoon, maar van `remote/api/oauth.js`
    ná een geslaagde Google/Microsoft-consent-redirect (zie CLAUDE.md 14 —
    de lokale instance blijft zo dicht; alleen de publiek bereikbare Vercel-
    app hoeft de OAuth-redirect te ontvangen). Payload:
    {site_id, provider, account_email, credentials: {access_token,
    refresh_token, expiry, scopes}, scopes: [...]}.

    De payload draagt een refresh-token — na een geslaagde apply scrubt
    `remote/api/bridge.js:ack()` 'm bewust uit Neon (zie dat bestand); hier
    hoeft niets extra's te gebeuren, dit is gewoon de schrijfkant."""
    from ..onboarding import resolve

    site_id = str(payload.get("site_id") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    credentials = payload.get("credentials") or {}
    if not site_id or provider not in ("google", "microsoft"):
        return False, "Onvolledige payload (site_id/provider ontbreekt of onbekend)"
    if not credentials.get("access_token") or not credentials.get("refresh_token"):
        return False, "Geen bruikbare tokens meegegeven"
    resolve.store_relayed_token(
        site_id, provider,
        str(payload.get("account_email") or ""),
        credentials,
        list(payload.get("scopes") or credentials.get("scopes") or []),
    )
    return True, f"{provider.capitalize()} gekoppeld"


# Commando's staan bewust in een eigen tabel: ze horen niet bij één item, en
# een tikfout mag nooit per ongeluk in de item-whitelist vallen.
async def _cmd_calendar_add(payload: Dict) -> Tuple[bool, str]:
    """Vrije-tekst / spraak opdracht -> agenda-voorstel (review-gate).

    Payload: {'text': 'dinsdag 18 augustus om 12.15 naar de tandarts'} of
    {'text': 'blok alle dinsdagen tussen 09.00 en 10.00'}.
    Parsed naar een afspraak, conflict-gecontroleerd, en neergelegd als
    calendar_proposal (status=pending_review) — boeken gebeurt pas als Vincent
    het voorstel in Iris Remote goedkeurt.
    """
    text = (payload.get("text") or "").strip()
    if not text:
        return False, "Geen opdracht meegegeven (verwacht payload.text)"
    from ...domains.calendar import nl_command as nlc
    from ...domains.calendar import agent as cal_agent
    from ...shared.database import get_conn

    cmd = nlc.parse_command(text)
    if cmd.kind == "error":
        return False, cmd.error or "Kon de opdracht niet lezen"
    cmd = nlc.check_conflict(cmd)

    # Dezelfde opdracht twee keer indienen (dubbele tik op de knop, of een
    # spraakopname die twee keer binnenkomt) mag geen twee voorstellen
    # opleveren. Gemeten 11 aug 2026: "blok alle dinsdagen tussen 09.00 en
    # 10.00" werd 2 minuten na elkaar ingediend, allebei goedgekeurd 11
    # seconden na elkaar — een wekelijkse dinsdagblokkade staat sindsdien
    # dubbel geboekt. Zelfde doelvenster (start/eind/weekdag) binnen een
    # kwartier = hetzelfde voorstel, ongeacht kleine tekstverschillen.
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT id, title, status FROM calendar_proposals "
            "WHERE mailbox_id='iris-command' AND status IN ('pending_review','booked') "
            "AND created_at >= datetime('now', '-15 minutes') "
            "AND proposed_start = ? AND proposed_end = ? "
            "AND COALESCE(recur_weekday, -1) = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (cmd.start.isoformat(), cmd.end.isoformat(),
             cmd.recur_weekday if cmd.recur_weekday is not None else -1),
        ).fetchone()
    if dup:
        stand = "al geboekt" if dup["status"] == "booked" else "wacht nog op jouw goedkeuring"
        return False, (f"Dit voorstel bestaat al (#{dup['id']} '{dup['title']}', {stand}). "
                        "Nog een keer indienen zou hetzelfde moment dubbel boeken — "
                        "keur het bestaande voorstel goed/af in plaats van dit te herhalen.")

    # Bouw rationale (conflict-analyse voor de mens).
    conflict_txt = ""
    if cmd.conflict:
        st = cmd.conflict.get("status")
        ov = cmd.conflict.get("overlaps") or []
        if ov:
            conflict_txt = ("LET OP: overlap met bestaande afspraak " +
                            "; ".join(f"{c.get('start')}–{c.get('end')}" for c in ov[:2]) +
                            ". Verplaats of kies een ander slot.")
            cmd.title = cmd.title  # conflict blijft zichtbaar in de titel-context
        elif st == "unavailable":
            conflict_txt = "Niet op dubbele boeking gecontroleerd: geen agenda gekoppeld."
        elif st == "error":
            conflict_txt = "Niet op dubbele boeking gecontroleerd: agenda-check mislukte."

    recur = cmd.recur_weekday
    recur_count = cmd.recur_count
    if cmd.all_day:
        tijdvak = "hele dag (00:00-24:00)"
    else:
        tijdvak = f"{cmd.start.strftime('%H:%M')}-{cmd.end.strftime('%H:%M')} ({cmd.duration_min} min)"
    rationale = (
        f'Spraak/tekst-opdracht: "{cmd.raw}". '
        f"Voorgesteld: {cmd.start.strftime('%a %d-%m')} {tijdvak} "
        f"Locatie: {'Online' if cmd.is_remote else (cmd.location or 'niet genoemd')}. "
        + (f"Terugkerend: elke {_wd_nl(recur)}" + (f" ({recur_count} keer)" if recur_count else "") + "." if recur is not None else "")
        + (f" {conflict_txt}" if conflict_txt else " Geen conflict gevonden.")
    )
    # Titel: parse_command levert al '(wekelijks)' bij recursief; niet nog een
    # keer dubbel plakken.
    title = cmd.title
    if recur is not None and not title.endswith("(wekelijks)"):
        title = f"{title} (wekelijks)"

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO calendar_proposals
               (mailbox_id, inbox_id, from_addr, subject, title,
                proposed_start, proposed_end, location, is_remote,
                duration_min, travel_buffer_min, priority, conflict_note,
                conflict_checked, rationale, recur_weekday, recur_count, all_day, status, created_at)
               VALUES ('iris-command', 0, 'iris-command', ?, ?, ?, ?, ?, ?,
                       ?, 0, 'normal', ?, ?, ?, ?, ?, ?, 'pending_review', datetime('now'))""",
            (text[:120], title,
             cmd.start.isoformat(), cmd.end.isoformat(),
             "Online" if cmd.is_remote else (cmd.location or ""),
             1 if cmd.is_remote else 0, cmd.duration_min,
             conflict_txt, cmd.conflict.get("status") if cmd.conflict else "ok",
             rationale, recur if recur is not None else -1,
             recur_count if recur_count is not None else -1,
             1 if cmd.all_day else 0),
        )
        pid = cur.lastrowid

    when = _nl_date(cmd.start)
    kind = "wekelijks terugkerend blok" if recur is not None else "afspraak"
    conflict_flag = " ⚠️ CONFLICT" if (cmd.conflict and cmd.conflict.get("overlaps")) else ""
    return True, (f"Voorstel {kind} aangemaakt: '{title}' op {when}.{conflict_flag} "
                  f"Keur goed in Iris Remote om te boeken.")


def _wd_nl(num: Optional[int]) -> str:
    return {0: "maandag", 1: "dinsdag", 2: "woensdag", 3: "donderdag",
            4: "vrijdag", 5: "zaterdag", 6: "zondag"}.get(num or 0, "?")


_NL_MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
              "augustus", "september", "oktober", "november", "december"]


def _nl_date(dt) -> str:
    """NL datum zonder locale-afhankelijkheid (strftime geeft hier Engels)."""
    return f"{_wd_nl(dt.weekday())} {dt.day} {_NL_MONTHS[dt.month - 1]}"


_COMMANDS = {
    "content_run": _cmd_content_run,
    "outreach_run": _cmd_outreach_run,
    "seo_refresh": _cmd_seo_refresh,
    "lead_search": _cmd_lead_search,
    "linkbuilding_run": _cmd_linkbuilding_run,
    "mail_sync": _cmd_mail_sync,
    "mail_rule": _cmd_mail_rule,
    "mail_archive": _cmd_mail_archive,
    "helpdesk_run": _cmd_helpdesk_run,
    "iris_briefing": _cmd_iris_briefing,
    "context_refresh": _cmd_context_refresh,
    "digest": _cmd_digest,
    "orchestrator_run": _cmd_orchestrator_run,
    "calendar_add": _cmd_calendar_add,
    "ritual_morning_save": _cmd_ritual_morning_save,
    "ritual_evening_save": _cmd_ritual_evening_save,
    "ritual_win_add": _cmd_ritual_win_add,
    "ritual_goal_progress": _cmd_ritual_goal_progress,
    "onboarding_step1": _cmd_onboarding_step1,
    "onboarding_step2": _cmd_onboarding_step2,
    "onboarding_step4": _cmd_onboarding_step4,
    "onboarding_complete": _cmd_onboarding_complete,
    "onboarding_new_client": _cmd_onboarding_new_client,
    "oauth_token_relay": _cmd_oauth_token_relay,
}


# (item_kind = dismiss_kind uit het Actiecentrum, action) → uitvoerder.
_HANDLERS = {
    ("content", "approve"): _content_approve,
    ("content", "reject"): _content_reject,
    ("mail", "send"): _mail_send,
    ("mail", "reject"): _mail_reject,
    ("mail", "edit"): _mail_edit,
    ("personal_mail", "send"): _personal_mail_send,
    ("personal_mail", "reject"): _personal_mail_reject,
    ("outreach", "approve"): _outreach_approve,
    ("outreach", "reject"): _outreach_reject,
    ("calendar", "approve"): _calendar_approve,
    ("calendar", "reject"): _calendar_reject,
}

# Weggeklikken mag voor elk item-type dat het Actiecentrum kent. `scheduler`
# hoorde er vanaf het begin bij te staan (build_inbox produceert die kaarten)
# maar ontbrak — een scheduler-fout was daardoor het enige item op de telefoon
# waarvan zelfs 'Wegklikken' een fout gaf.
_DISMISSABLE = {"content", "mail", "personal_mail", "outreach", "calendar", "goal", "task",
                "error", "vacancies", "leads", "linkbuilding", "scheduler"}


async def apply_decision(decision: Dict[str, Any]) -> Tuple[bool, str]:
    kind = str(decision.get("item_kind") or "")
    action = str(decision.get("action") or "")
    item_id = str(decision.get("item_id") or "")
    payload = decision.get("payload") or {}

    # Commando's horen niet bij een item: kind='command', action=de opdracht.
    if kind == "command":
        handler = _COMMANDS.get(action)
        if not handler:
            return False, f"Onbekend commando '{action}'"
        try:
            return await handler(payload)
        except ValueError as e:
            return False, str(e)[:300]
        except Exception as e:  # noqa: BLE001
            logger.exception("Bridge-commando mislukt: %s", action)
            return False, f"Fout bij uitvoeren: {str(e)[:250]}"

    if not kind or not action or not item_id:
        return False, "Onvolledig besluit (kind/action/id ontbreekt)"

    if action == "dismiss":
        if kind not in _DISMISSABLE:
            return False, f"Onbekend item-type '{kind}' voor dismiss"
        return await _dismiss(kind, item_id)

    handler = _HANDLERS.get((kind, action))
    if not handler:
        return False, f"Actie '{action}' op '{kind}' staat niet op de whitelist"
    try:
        return await handler(item_id, payload)
    except ValueError as e:
        return False, str(e)[:300]
    except Exception as e:
        logger.exception("Bridge-besluit mislukt: %s/%s op %s", kind, action, item_id)
        return False, f"Fout bij uitvoeren: {str(e)[:250]}"
