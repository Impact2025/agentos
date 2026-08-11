"""
Wat er niet gebeurde toen de machine uit stond.

Aanleiding (2 aug 2026): het dashboard van WeAreImpact toonde één gemiste
scheduler-run van 22 juli, met de geruststelling "draait bij de volgende
geplande run vanzelf". Ondertussen was de machine van 28 t/m 31 juli vier
werkdagen aaneengesloten uit geweest. In die dagen vuurde de outreach-batch
(ma-vr 07:15) vier keer niet, sloeg de vacaturescan van donderdag over, en
was de linkbuilding-weekrun van woensdag 29 juli ook al de tweede die
overging — een job die volgens `scheduler_runs.last_ok_at` nog nóóit was
geslaagd. Nergens stond dat. Het énige spoor was een melding die beweerde
dat het vanzelf goed kwam.

Waarom de bestaande inhaalslag dit niet dekt (en niet moet dekken): die
haalt bewust alleen de runs van vandáág op. Een ochtendrapport van gisteren
alsnog mailen helpt niemand — de opbrengst van zulke jobs veroudert per
dag. Maar dat geldt niet voor álle jobs. Een outreach-batch die niet
draaide is een dag zónder concepten in de trechter, en die dag komt nooit
meer terug; de acquisitieformule meet input tegen output, dus vier lege
dagen vervalsen de hele meting. Dát verschil legt deze module vast.

Drie regels:

1. **Alleen wat waarde houdt.** Een JobSpec zonder `gap_cost` levert iets
   op dat morgen vanzelf weer vers is (rapporten, briefings, syncs die een
   venster terugkijken). Die gemiste runs worden geregistreerd maar nooit
   gemeld — anders staat het Actiecentrum na elk weekend vol met ruis waar
   niemand iets aan kan doen.
2. **Melden mét de knop die het repareert, via één weg.** Een kaart die zegt
   "de outreach-batch heeft vier dagen niet gedraaid" en je vervolgens laat
   zoeken, is een verwijt. De kaart draagt daarom een actie die de job
   alsnog draait; dat is het enige dat de schade nog terughaalt. Die kaart
   maakt het Actiecentrum rechtstreeks uit `scheduler_gaps` — deze module
   schrijft er géén uitkomstkaart bij. Tot 2 aug 2026 deed ze dat wel, met
   dezelfde filter en woordelijk dezelfde zin, en stond elke gemiste taak
   dubbel in de inbox: één keer met "Nu alsnog draaien" en één keer met
   "Analyseer & fix", een knop die hier niets kán doen (de oorzaak is een
   machine die uit stond). Twee meldwegen naar één beslissing is hoe een
   inbox onleesbaar wordt. De invariant `stilstand_dubbel_gemeld` bewaakt
   dat er geen derde bij komt.
3. **Een gat sluit zichzelf.** Zodra de job weer slaagt, gaan de open
   gaten dicht en verdwijnt de kaart. Een rode kaart die blijft staan voor
   iets dat allang is opgelost, is precies de ruis die het Actiecentrum
   onleesbaar maakt (zie `shared/failures.py`, 25 jul 2026).

Los daarvan staat de tweede bevinding die alleen bij het opstarten te zien
is: een job die wél is ingepland maar `last_ok_at IS NULL` heeft. Dat is
geen stilstand maar een defect, en dat escaleert meteen — wachten repareert
het niet.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .database import get_conn

logger = logging.getLogger(__name__)

# Hoe ver terug we naar gemiste vuurmomenten kijken. Twee weken dekt een
# vakantie en een lang weekend; verder terug is archeologie waar geen
# inhaalactie meer bij past.
LOOKBACK_DAYS = 14


def _uid(job_id: str, scheduled_for: str) -> str:
    return hashlib.sha1(f"{job_id}|{scheduled_for}".encode("utf-8")).hexdigest()[:16]


def record_gap(job_id: str, label: str, scheduled_for: datetime,
               cost: str = "", recoverable: bool = False) -> bool:
    """Leg één gemist vuurmoment vast. Idempotent.

    Geeft True terug als dit een níeuw gat was — zo kan de aanroeper het
    verschil zien tussen "we kijken nog eens naar hetzelfde" en "er is iets
    bijgekomen", zonder dat te hoeven afleiden uit tellingen.
    """
    gid = _uid(job_id, scheduled_for.isoformat())
    now = datetime.now().astimezone().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO scheduler_gaps
               (id, job_id, label, scheduled_for, detected_at, cost, recoverable)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (gid, job_id, label, scheduled_for.isoformat(), now, cost,
             1 if recoverable else 0),
        )
        return cur.rowcount > 0


def mark_recovered(job_id: str) -> int:
    """Sluit alle open gaten van een job — hij heeft weer gedraaid.

    Aangeroepen bij élke geslaagde run, niet alleen bij een expliciete
    inhaalactie. Of het gat nu dichtging doordat Vincent op de knop drukte
    of doordat de volgende geplande run gewoon slaagde, maakt voor de kaart
    niets uit: het werk is gedaan.
    """
    now = datetime.now().astimezone().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE scheduler_gaps SET recovered_at = ? "
            "WHERE job_id = ? AND recovered_at IS NULL", (now, job_id),
        )
        return cur.rowcount


def open_gaps(only_reportable: bool = True) -> List[Dict]:
    """Openstaande gaten, nieuwste eerst."""
    sql = "SELECT * FROM scheduler_gaps WHERE recovered_at IS NULL"
    if only_reportable:
        # Een gat zonder kostenregel is geregistreerd maar niet meldenswaardig:
        # de opbrengst van die job is morgen vanzelf weer vers.
        sql += " AND cost != ''"
    sql += " ORDER BY scheduled_for DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql)]


def summary() -> List[Dict]:
    """Per job: hoeveel runs gemist, sinds wanneer, en wat dat kostte.

    Dit is de vorm waarin het Actiecentrum en Iris het lezen — één regel per
    job, niet één per gemist vuurmoment. Vier losse kaarten voor vier dagen
    dezelfde stilstand zeggen niet méér dan één kaart die "vier keer" zegt,
    en ze verdringen wel vier andere dingen van het scherm.
    """
    grouped: Dict[str, Dict] = {}
    for gap in open_gaps():
        entry = grouped.setdefault(gap["job_id"], {
            "job_id": gap["job_id"],
            "label": gap["label"],
            "cost": gap["cost"],
            "recoverable": bool(gap["recoverable"]),
            "missed": 0,
            "first": gap["scheduled_for"],
            "last": gap["scheduled_for"],
        })
        entry["missed"] += 1
        entry["first"] = min(entry["first"], gap["scheduled_for"])
        entry["last"] = max(entry["last"], gap["scheduled_for"])
    out = sorted(grouped.values(), key=lambda e: -e["missed"])
    for e in out:
        e["detail"] = describe(e)
    return out


def _dutch_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d-%m")
    except (ValueError, TypeError):
        return iso[:10]


def describe(entry: Dict) -> str:
    """Eén zin die zegt wat er níet gebeurde, in werk in plaats van in runs."""
    n = entry["missed"]
    periode = (f"op {_dutch_date(entry['last'])}" if n == 1
               else f"{n}× tussen {_dutch_date(entry['first'])} en {_dutch_date(entry['last'])}")
    return f"{entry['label']} draaide {periode} niet — {entry['cost']}"


# ── Escalatie ───────────────────────────────────────────────────────────────

def never_succeeded(runs: Dict[str, Dict], job_ids: List[str]) -> List[str]:
    """Jobs die écht zijn uitgevoerd en toch nog nooit slaagden.

    `last_ok_at IS NULL` bij een job die wél een `last_run_at` heeft, is het
    duidelijkste defectsignaal dat dit systeem kent — en het stond nergens.
    De linkbuilding-weekrun leefde er maanden op: elke week een 'missed' met
    de tekst dat het vanzelf goed zou komen, terwijl er nooit één geslaagde
    run had bestaan om op terug te vallen.

    Wat dit níet is: een taak die alleen maar gemíst is. Die heeft nooit
    gedraaid en is dus ook niet aantoonbaar stuk — daar hoort de stilstand-kaart
    met de inhaalknop bij, niet een defectmelding. Sinds 2 aug 2026 zet een
    misfire `last_run_at` niet meer (scheduler._record_run), waardoor het
    onderscheid hier vanzelf klopt; daarvóór noemde deze functie elke taak die
    tijdens een uitgezette machine overging "defect".
    """
    kapot = []
    for jid in job_ids:
        run = runs.get(jid)
        if run and run.get("last_run_at") and not run.get("last_ok_at"):
            kapot.append(jid)
    return kapot


def report_never_succeeded(job_id: str, label: str, last_error: str = "") -> None:
    from .outcomes import log_outcome
    vandaag = datetime.now().astimezone().date().isoformat()
    with get_conn() as conn:
        bestaat = conn.execute(
            "SELECT 1 FROM activity_log WHERE action = 'job_nooit_geslaagd' "
            "AND detail LIKE ? AND substr(created_at, 1, 10) = ?",
            (f"{job_id}|%", vandaag),
        ).fetchone()
    if bestaat:
        return
    log_outcome(
        project="Scheduler",
        action="job_nooit_geslaagd",
        detail=(f"{job_id}| Geplande taak '{label}' is sinds zijn bestaan nog nooit "
                f"geslaagd." + (f" Laatste fout: {last_error[:200]}" if last_error else "")),
        artifact="/api/scheduler/gaps",
        next_step=("Draai hem handmatig en lees de fout — wachten op de volgende "
                   "geplande run heeft hier nog nooit gewerkt."),
        status="error",
    )


def prune(days: int = 60) -> int:
    """Ruim gedichte gaten op die niemand meer leest."""
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM scheduler_gaps WHERE recovered_at IS NOT NULL AND recovered_at < ?",
            (cutoff,),
        )
        return cur.rowcount
