"""Iris API — de manager-agent.

  GET  /api/iris/briefing   → laatste dagbriefing (of live cijfer-snapshot)
  GET  /api/iris/history    → briefing-geschiedenis (cijfers/lessen/advies per dag)
  GET  /api/iris/scores     → actuele deterministische cijfers per project
  GET  /api/iris/lessons    → actieve lessen uit haar geheugen
  POST /api/iris/run-now    → dagbriefing nu draaien (analyse + bijsturing)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict

from . import metrics, service

router = APIRouter(prefix="/api/iris", tags=["iris"])


@router.get("/briefing")
def briefing():
    from . import predictions
    track_record = predictions.track_record()
    report = service.latest_report()
    if report:
        report["track_record"] = track_record
        return report
    # Nog geen briefing gedraaid: geef alvast het cijferbeeld zodat de UI
    # nooit leeg is.
    return {"report_date": None, "markdown": "", "grades": {},
            "track_record": track_record, "metrics": metrics.snapshot()}


@router.get("/history")
def history(limit: int = Query(14, ge=1, le=60)):
    return {"reports": service.report_history(limit)}


@router.get("/scores")
def scores():
    return metrics.snapshot()


@router.get("/lessons")
def lessons():
    return {"lessons": service.active_lessons(limit=50)}


@router.get("/trends")
def trends():
    """Week-over-week GSC-delta's per project (site-trend + pagina-bewegers)."""
    from ..seo import history as history_service
    from ..seo import sites as sites_service
    out = []
    for s in sites_service.list_sites():
        if not (s.get("gsc_property") or "").strip():
            continue
        out.append({
            "site_id": s["id"],
            "name": s["name"],
            "trend": history_service.site_trend(s["id"]),
            "movers": history_service.page_movers(s["id"], limit=5),
        })
    return {"projects": out}


@router.get("/gsc-series/{site_id}")
def gsc_series(site_id: str, days: int = Query(28, ge=7, le=90)):
    """Dagreeks (clicks/impressies/CTR/positie) voor een trendgrafiek."""
    from ..seo import history as history_service
    return {"site_id": site_id, "series": history_service.site_series(site_id, days=days)}


@router.get("/predictions")
def predictions_view():
    """Iris' gesloten leer-lus: haar eigen trefkans + de openstaande
    voorspellingen die nog afgerekend worden."""
    from . import predictions
    return {"track_record": predictions.track_record(),
            "open": predictions.open_predictions()}


# ── Kennisbank: Vincent voedt Iris met onderzoek ────────────────────────────

class ManualNote(BaseModel):
    title: str = ""
    text: str


@router.get("/knowledge")
def knowledge_list():
    """Actieve kennisitems + het pad van de vault-map om onderzoek in te droppen."""
    from . import knowledge
    return {"folder": knowledge.ensure_folder(), "items": knowledge.list_knowledge()}


@router.post("/knowledge/sync")
async def knowledge_sync():
    """Scan de vault-map opnieuw en distilleer nieuwe/gewijzigde onderzoeksdocs."""
    from . import knowledge
    return await knowledge.sync_knowledge()


@router.post("/knowledge")
async def knowledge_add(body: ManualNote):
    """Voeg kennis direct toe (geplakt), zonder een vault-bestand aan te maken."""
    from . import knowledge
    kid = await knowledge.add_manual_note(body.title, body.text)
    if not kid:
        raise HTTPException(status_code=400, detail="Te weinig tekst om iets van te leren")
    return {"id": kid, "items": knowledge.list_knowledge()}


@router.delete("/knowledge/{kid}")
def knowledge_delete(kid: str):
    from . import knowledge
    if not knowledge.delete_knowledge(kid):
        raise HTTPException(status_code=404, detail="Kennisitem niet gevonden")
    return {"success": True}


@router.post("/run-now")
async def run_now():
    """Draai de dagbriefing direct (zelfde flow als de 06:45-job)."""
    return await service.run_morning_briefing()

# ── Actie-voorstellen ("Wil je dat ik dit fix?") ───────────────────────
# Iris legt kant-en-klare fixes klaar; Vincent keurt per stuk goed.
@router.get("/suggestions")
def suggestions_list(report_date: Optional[str] = Query(None)):
    """Lijst actie-voorstellen (optioneel: alleen die van één briefing)."""
    from . import fix as fix_service
    return {"suggestions": fix_service.list_pending(report_date)}

@router.post("/suggestions/{sid}/approve")
async def suggestions_approve(sid: str):
    """Keur een actie goed — nog NIET uitgevoerd (wacht op apply)."""
    from . import fix as fix_service
    if not fix_service.approve(sid):
        raise HTTPException(status_code=404, detail="Actie niet gevonden")
    return {"ok": True, "status": "approved"}

@router.post("/suggestions/{sid}/reject")
async def suggestions_reject(sid: str):
    """Wijs een actie af (blijft gesloten, komt niet terug)."""
    from . import fix as fix_service
    if not fix_service.reject(sid):
        raise HTTPException(status_code=404, detail="Actie niet gevonden")
    return {"ok": True, "status": "rejected"}

@router.post("/suggestions/{sid}/apply")
async def suggestions_apply(sid: str):
    """Voer een GOEDGEKEURDE actie uit via de agents (achter review-gate)."""
    from . import fix as fix_service
    result = await fix_service.apply(sid)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Uitvoering mislukt"),
        )
    return {"ok": True, **result}


# ── Zelfherstel: Iris lost fouten zelf op, ongevraagd ───────────────────────
@router.get("/search-provider-health")
def search_provider_health():
    """Live-status van elke zoekprovider (key/quota + echte bereikbaarheid).

    Geeft per provider `status` ('ok' | 'no_key' | 'quota' | 'down'),
    de geconfigureerde keten-volgorde en welke provider de laatste zoekopdracht
    daadwerkelijk leverde. Zo ziet Iris/dashboard in één oogopslag of de
    zoeklaag gezond is of dat een provider verborgen dood is."""
    from ...shared import websearch as w
    import time as _t
    configured = w.providers_configured()
    live = w.probe_health()
    status = {}
    for name in configured:
        if live.get(name) == "down":
            status[name] = "down"
        else:
            status[name] = w.provider_health().get(name, "ok")
    return {"configured_chain": configured,
            "live_health": live,
            "status": status,
            "quota_blocked": {k: int(_t.time() < v)
                              for k, v in w._quota_block.items()}}


@router.post("/search-provider-health/heal")
def search_provider_heal():
    """Dwing de watchdog: meet alle providers live en hef herstelde blokkades op."""
    from ...shared import websearch as w
    lifted = w.clear_recovered_blocks()
    return {"ok": True, "lifted": lifted}


@router.get("/selfheal")
def selfheal_log(limit: int = Query(20, ge=1, le=100)):
    """Logboek: wat probeerde Iris, wat lukte, en wat moest ze melden."""
    from . import selfheal
    return {"items": selfheal.recent_heals(limit)}


@router.post("/selfheal/run")
async def selfheal_run():
    """Draai de zelfherstel-ronde nu (draait ook elke 10 min automatisch)."""
    from . import selfheal
    return await selfheal.run_selfheal(source="handmatig")


# ── Waarheidsaudit: wat is er stil kapot? ──────────────────────────────────
@router.get("/integrity")
def integrity_status(severity: str = Query("", pattern="^(blokkerend|stil|hygiene)?$"),
                     limit: int = Query(200, ge=1, le=500)):
    """Openstaande bevindingen + de stand per invariant.

    `invarianten` gaat mee in het antwoord zodat de UI kan laten zien wáárom een
    toets bestaat: elke regel codeert een storing die echt is voorgekomen. Een
    audit waarvan niemand de herkomst kent, wordt genegeerd zodra hij een keer
    ongelegen komt.
    """
    from . import integrity
    return {
        "samenvatting": integrity.audit_summary(),
        "bevindingen": integrity.open_findings(severity=severity, limit=limit),
        "invarianten": [
            {"key": i.key, "titel": i.titel, "severity": i.severity,
             "incident": i.incident, "stap": i.stap}
            for i in integrity.INVARIANTEN
        ],
    }


@router.post("/integrity/run")
def integrity_run():
    """Draai de audit nu (draait ook dagelijks 06:40 en bij elke briefing)."""
    from . import integrity
    return integrity.run_audit(source="handmatig")


@router.post("/integrity/repair/{invariant}")
async def integrity_repair(invariant: str, project: str = Query(""),
                           maximum: int = Query(25, ge=1, le=100)):
    """Repareer de openstaande bevindingen van één invariant.

    De andere helft van de audit. Tot 4 aug 2026 leverde élke invariant alleen
    een kaart op: er stonden 82 bevindingen open en er bestond geen enkele
    remedie in de codebase, ook niet via zelfherstel (`waarheidsaudit` staat in
    `_MENSELIJK_BESLUIT`). Elke toets die erbij kwam, werd zo een to-do voor een
    mens in plaats van werk voor een agent.

    Reparaties lopen via de gewone publicatieroute mét alle gates — nooit via
    rechtstreekse HTTP, want dat is precies hoe een eenmalig reparatiescript op
    23 juli een niet-publiceerbare taaktitel live zette. En de bevinding wordt
    hier niet gesloten: dat doet de audit als hij hem niet meer vindt.
    """
    from ..publish import repair

    remedies = repair.REMEDIES
    doe = remedies.get(invariant)
    if not doe:
        raise HTTPException(
            status_code=404,
            detail=(f"Voor '{invariant}' bestaat nog geen automatische remedie. "
                    f"Beschikbaar: {', '.join(sorted(remedies)) or '(geen)'}."))
    return await doe(project=project or None, maximum=maximum)


# ── "Analyseer & fix" — vanuit een bestaande foutkaart in het Actiecentrum ──
@router.post("/errors/{error_id}/triage")
async def errors_triage(error_id: str, kind: str = Query("activity_log")):
    """Diagnosticeer een foutmelding en voer de remedie meteen uit (of leg de
    mens-stap vast). Eén klik = goedkeuring; de remedie zelf blijft achter de
    bestaande review-gates. kind='content_job' voor mislukte publicaties
    (andere id-namespace dan activity_log)."""
    from . import triage
    result = await triage.analyze_and_fix(error_id, kind=kind)
    if not result.get("ok") and "diagnosis" not in result:
        raise HTTPException(status_code=400, detail=result.get("error", "Analyseren mislukt"))
    return result


@router.post("/errors/triage-all")
async def errors_triage_all(body: Optional[Dict] = None):
    """Bulk-variant: analyseer én herstel alle foutkaarten in het Actiecentrum
    in één keer. VUUR-EN-VERGEET — komt meteen terug met een job_id; de
    verwerking loopt op de achtergrond. Patroon-errors (OpenModel-down,
    MS-auth, catch-up-timeout) worden deterministisch gediagnosticeerd, zonder
    een LLM per kaart. Ververs het Actiecentrum na enkele seconden."""
    from . import triage
    kinds = None
    if isinstance(body, dict):
        kinds = body.get("kinds")
    return await triage.analyze_and_fix_all(kinds=kinds)


@router.post("/errors/{error_id}/reconnect-microsoft")
async def errors_reconnect_microsoft(error_id: str):
    """Start de Microsoft device-code login (Outlook/Graph) voor een
    'Niet geauthenticeerd bij Microsoft'-fout. Geeft de user_code +
    verification_uri terug die Vincent in zijn browser invoert."""
    from ...domains.outlook import service as outlook_service
    if not outlook_service.is_configured():
        raise HTTPException(400, "OUTLOOK_CLIENT_ID niet ingesteld in .env")
    try:
        flow = outlook_service.prepare_device_flow()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Kon de Microsoft login niet starten: {e}")
    return {
        "ok": True,
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"),
        "expires_in": flow.get("expires_in"),
        "message": "Open de link, voer de code in, en de Bridge-mail-sync herstelt zichzelf.",
    }
