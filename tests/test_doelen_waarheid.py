"""Wat de Doelen-tab beweert, moet waar zijn.

Aanleiding (4 aug 2026): één screenshot van de Doelen-tab bevatte vier
onafhankelijke onwaarheden, allemaal van hetzelfde soort — de weergave en de
boekhouding vertelden iets dat de data niet droeg.

  * de kop zei "Bewaardvoorjou", de lijst toonde doelen van vier ándere
    projecten: de tab haalde `/api/goals` op zónder projectfilter, terwijl de
    API die filter wél kent;
  * de voortgang stond op "9/3" en "14/4": de teller telde taken, de noemer
    fases, en elke balk stond daardoor op meer dan 100%;
  * gefaalde taken (1 tot 4 per doel) stonden in de data en werden nergens
    getoond — juist bij 'partial', waar het hele verhaal in zit;
  * `Bewaard voor Jou` (11 doelen) en `Bewaardvoorjou` (9) waren twee
    administraties van één merk, dus zou een wérkende filter de helft van de
    historie hebben laten verdwijnen.

Daaronder lagen twee diepere storingen, die deze tests vooral vastleggen:

  * 21 voltooide publisher-taken claimden 6 Wachtrij-jobs, en één artikel stond
    19× in de Wachtrij — één effect, meervoudig opgeëist;
  * een gefaalde uitvoertaak werd via `_find_alternative` alsnog 'completed' met
    LLM-proza over het werk.
"""
import uuid

import pytest

from backend.domains.goal import service as goal_service
from backend.domains.projects import router as projects_router
from backend.shared.database import get_conn
from backend.shared.projects import canonical_project, squash_project


def _nieuw_doel(project: str, titel: str = "test", status: str = "completed",
                dagen_geleden: int = 1) -> str:
    goal_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goals (id, title, objective, project, status, created_at, updated_at) "
            "VALUES (?, ?, '', ?, ?, datetime('now', ?), datetime('now'))",
            (goal_id, titel, project, status, f"-{dagen_geleden} day"),
        )
    return goal_id


def _nieuwe_taak(goal_id: str, titel: str, result: str) -> None:
    """Voltooide publisher-taak, inclusief de fase waar de FK naar wijst."""
    fase_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goal_phases (id, goal_id, title, status, ord, created_at, updated_at) "
            "VALUES (?, ?, '__test__fase', 'completed', 1, datetime('now'), datetime('now'))",
            (fase_id, goal_id),
        )
        conn.execute(
            "INSERT INTO goal_tasks (id, goal_id, phase_id, title, description, skill, "
            "status, result, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, '__test__', 'publisher', 'completed', ?, datetime('now'), datetime('now'))",
            (str(uuid.uuid4()), goal_id, fase_id, titel, result),
        )


@pytest.fixture(autouse=True)
def _schoon():
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM goals WHERE objective = ''")
        conn.execute("DELETE FROM goal_tasks WHERE description = '__test__'")
        conn.execute("DELETE FROM goal_phases WHERE title = '__test__fase'")
        conn.execute("DELETE FROM sites WHERE id = '__test__site'")
        conn.execute("DELETE FROM content_jobs WHERE rationale LIKE '%__test__%'")


# ── Eén project, één administratie ─────────────────────────────────────────

def test_spellingsvarianten_zijn_hetzelfde_project():
    """'Bewaard voor Jou' en 'Bewaardvoorjou' zijn één merk.

    Nederlandse merknamen worden even vaak aaneen als los geschreven, en beide
    vormen zijn ooit ergens ingetypt. Zonder deze gelijkstelling verdwijnt de
    helft van de historie zodra de filter gaat werken.
    """
    assert squash_project("Bewaard voor Jou") == squash_project("Bewaardvoorjou")
    assert squash_project("WeAreImpact") == squash_project("weareimpact")
    assert squash_project("Steentjebij Steentje") == squash_project("Steentjebijsteentje")
    # Maar géén valse gelijkstelling: dit zijn echt twee projecten.
    assert squash_project("Daar") != squash_project("daarwebsite")


def test_canonieke_naam_raadt_niet():
    """Een project dat `sites` niet kent, houdt zijn eigen naam.

    Raden is hier erger dan niets doen: een intern project ('Systeem') mag niet
    stilzwijgend aan een klantsite worden geplakt.
    """
    assert canonical_project("Nietbestaandproject Xyz",
                             known=["WeAreImpact"]) == "Nietbestaandproject Xyz"
    assert canonical_project("bewaardvoorjou",
                             known=["Bewaard voor Jou"]) == "Bewaard voor Jou"


def test_list_goals_vindt_beide_spellingen():
    """De filter mag geen historie verbergen die anders gespeld is."""
    _nieuw_doel("Bewaard voor Jou", "doel met spaties")
    _nieuw_doel("Bewaardvoorjou", "doel aaneen")

    titels = {g["title"] for g in goal_service.list_goals(limit=50, project="Bewaardvoorjou")}
    assert {"doel met spaties", "doel aaneen"} <= titels, (
        "een projectfilter die maar één spelling kent laat de andere helft "
        "onbereikbaar achter — precies wat radar/models.py in juli al opruimde"
    )


def test_list_goals_filtert_wel_degelijk():
    """Zonder deze test is 'de filter werkt' een aanname.

    De oorspronkelijke bug zat in de frontend (geen `project=` meegestuurd),
    maar een backend die stilzwijgend alles teruggeeft maakt hem onzichtbaar.
    """
    _nieuw_doel("WeAreImpact", "van weareimpact")
    _nieuw_doel("Pootgelukkig", "van pootgelukkig")
    titels = {g["title"] for g in goal_service.list_goals(limit=50, project="Pootgelukkig")}
    assert "van pootgelukkig" in titels
    assert "van weareimpact" not in titels


# ── Eén effect wordt één keer opgeëist ─────────────────────────────────────

def test_bron_wordt_maar_een_keer_gestaged():
    """Een artikel dat al in de Wachtrij staat, is geen nieuw artikel.

    Elke publisher-taak pakte de nieuwste content-taak van het doel zonder bij
    te houden wat er al gestaged was. Een doel met "Publiceer artikel 1 t/m 19"
    leverde daardoor 19 identieke Wachtrij-jobs op en 19 taken die stuk voor
    stuk "ECHTE ACTIE UITGEVOERD" meldden.
    """
    goal_id = str(uuid.uuid4())
    bron_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sites (id, name, base_url, created_at) "
            "VALUES ('__test__site', 'Testsite', 'https://example.invalid', datetime('now'))")
        conn.execute(
            "INSERT INTO content_jobs (id, site_id, title, status, rationale, created_at) "
            "VALUES (?, '__test__site', 'Artikel', 'pending_review', ?, datetime('now'))",
            (str(uuid.uuid4()),
             f"__test__ Uit goal {goal_id} — publisher-taak 'x' "
             f"[{goal_service._BRON_MARKER}{bron_id}]"),
        )
    assert bron_id in goal_service._reeds_gestagede_bronnen(goal_id)
    assert goal_service._reeds_gestagede_bronnen(str(uuid.uuid4())) == set(), (
        "de zeef mag alleen binnen hetzelfde doel gelden — een ander doel dat "
        "hetzelfde onderwerp behandelt heeft zijn eigen artikel"
    )


def test_onleesbare_wachtrij_blokkeert_het_stagen_niet():
    """Kan de Wachtrij niet gelezen worden, dan is 'niets gestaged' het veilige
    antwoord — nooit 'alles al gestaged', want dan publiceert het systeem niets
    meer en meldt het dat als succes."""
    assert goal_service._reeds_gestagede_bronnen("") == set()


# ── Een vastloper opnieuw starten is geen actie ────────────────────────────

def test_alert_wijst_naar_de_vastloper_in_plaats_van_een_nieuwe_poging():
    """Na twee gestrande pogingen verandert de knop, niet de melding.

    De alert zelf moet blijven staan: het probleem is echt en 'partial' dempt
    terecht niet. Maar 'Verbeter de CTR van WeAreImpact' zeven keer starten en
    zeven keer stranden is geen zeven pogingen — het is één onopgelost probleem
    met zes overbodige rekeningen eronder.
    """
    goals = [
        {"id": "g1", "title": "Actiepunt: Verbeter de CTR van WeAreImpact",
         "objective": "", "status": "partial",
         "created_at": _iso_dagen_geleden(2)},
        {"id": "g2", "title": "Actiepunt: Verbeter de CTR van WeAreImpact",
         "objective": "", "status": "partial",
         "created_at": _iso_dagen_geleden(4)},
    ]
    origineel = {"type": "warning", "icon": "🎯", "text": "CTR 0.4% op positie 12",
                 "action": "fix_alert:Verbeter de CTR van WeAreImpact",
                 "action_label": "Oplossen"}
    uit = projects_router._knop_of_blokkade(
        origineel, goals, "Verbeter de CTR van WeAreImpact")

    assert uit["action"] == "open_goal:g1", "de knop moet naar de nieuwste vastloper wijzen"
    assert "CTR 0.4% op positie 12" in uit["text"], "de diagnose zelf blijft staan"
    assert "2 eerdere pogingen" in uit["text"]


def test_een_enkele_vastloper_blokkeert_nog_niet():
    """Eén mislukking kan pech zijn — een LLM-timeout, een lege gateway."""
    goals = [{"id": "g1", "title": "Verbeter de CTR van WeAreImpact", "objective": "",
              "status": "partial", "created_at": _iso_dagen_geleden(1)}]
    origineel = {"text": "CTR laag", "action": "fix_alert:Verbeter de CTR van WeAreImpact"}
    uit = projects_router._knop_of_blokkade(
        origineel, goals, "Verbeter de CTR van WeAreImpact")
    assert uit["action"].startswith("fix_alert:")


def test_geslaagde_pogingen_blokkeren_niet():
    """Alleen gestránde pogingen tellen. Een doel dat het onderwerp afrondde
    dempt de alert al via `_goal_addresses`; het mag hier geen blokkade worden."""
    goals = [
        {"id": "g1", "title": "Verbeter de CTR van WeAreImpact", "objective": "",
         "status": "completed", "created_at": _iso_dagen_geleden(1)},
        {"id": "g2", "title": "Verbeter de CTR van WeAreImpact", "objective": "",
         "status": "completed", "created_at": _iso_dagen_geleden(3)},
    ]
    uit = projects_router._knop_of_blokkade(
        {"text": "x", "action": "fix_alert:y"}, goals, "Verbeter de CTR van WeAreImpact")
    assert uit["action"] == "fix_alert:y"


def test_titelcap_is_gelijk_aan_de_frontend():
    """De dedupe vergelijkt op precies de titel die `shell.js` bouwt.

    Wijken de twee af, dan matcht de dedupe nooit en biedt het dashboard
    hetzelfde actiepunt eeuwig opnieuw aan. Deze test is de enige plek waar dat
    verband wordt gehandhaafd — het loopt door twee talen heen.
    """
    from pathlib import Path
    bron = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "shell.js").read_text(
        encoding="utf-8")
    assert f"objective.slice(0, {projects_router._ACTIEPUNT_TITELCAP})" in bron


def _iso_dagen_geleden(n: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# ── De invarianten zelf ────────────────────────────────────────────────────

@pytest.mark.parametrize("sleutel", [
    "effect_meervoudig_geclaimd",
    "uitvoertaak_zonder_uitvoering",
    "zelfde_actiepunt_opnieuw",
])
def test_nieuwe_invarianten_draaien(sleutel):
    """Een blinde toets die zwijgt is precies het probleem dat de audit zoekt."""
    from backend.domains.iris import integrity
    inv = next(i for i in integrity.INVARIANTEN if i.key == sleutel)
    assert inv.incident, "een toets zonder herkomst wordt bij de eerste ongelegen melding weggeklikt"
    inv.check()  # mag niet gooien op een lege database


def test_meervoudige_claim_wordt_gezien():
    """Twee voltooide taken die dezelfde Wachtrij-job opvoeren = één bevinding."""
    from backend.domains.iris import integrity
    goal_id = _nieuw_doel("WeAreImpact", "publiceerdoel", status="completed")
    job = "abcdef12-3456"
    for n in (1, 2):
        _nieuwe_taak(goal_id, f"Publiceer artikel {n}",
                     f'artikel "X" staat in de Wachtrij (job `{job}`, site WeAreImpact)')
    gevonden = [b for b in integrity._check_effect_meervoudig_geclaimd()
                if b.subject == f"job:{job}"]
    assert len(gevonden) == 1, "één job, één bevinding — niet één per taak"
    assert "2 voltooide publisher-taken" in gevonden[0].detail


def test_publisher_met_eigen_job_is_geen_bevinding():
    """Het normale geval mag niet meelopen, anders wordt de toets genegeerd."""
    from backend.domains.iris import integrity
    goal_id = _nieuw_doel("WeAreImpact", "publiceerdoel", status="completed")
    _nieuwe_taak(goal_id, "Publiceer artikel",
                 'artikel "X" staat in de Wachtrij (job `uniek-9999`, site WeAreImpact)')
    assert not [b for b in integrity._check_effect_meervoudig_geclaimd()
                if b.subject == "job:uniek-9999"]
    assert not [b for b in integrity._check_uitvoertaak_zonder_uitvoering()
                if b.project == "WeAreImpact" and "Publiceer artikel'" in b.detail]


# ── Wat de tweede ronde opleverde (WeAreImpact, 4 aug 2026) ────────────────

def test_dubbele_planning_wordt_gezien():
    """Eén doel met dezelfde taak twee keer = één bevinding, niet twee.

    Vijf doelen droegen hun volledige planning dubbel en voerden hem dus twee
    keer uit — 57 taakruns twee keer betaald. Het viel niet op omdat
    `task_count` de plánwaarde bewaart en het doel "26/14" meldde: dat las als
    een telfout in de weergave in plaats van als dubbel gedaan werk.
    """
    from backend.domains.iris import integrity
    goal_id = _nieuw_doel("WeAreImpact", "dubbelplan", status="partial")
    for _ in (1, 2):
        _nieuwe_taak(goal_id, "Zelfde taak", "resultaat")
    gevonden = [b for b in integrity._check_plan_dubbel_uitgevoerd()
                if b.subject == f"goal:{goal_id}"]
    assert len(gevonden) == 1
    assert "1 dubbele taak" in gevonden[0].detail


def test_stille_job_wordt_vergeleken_met_zijn_buren():
    """Een job die ophield vuren terwijl de rest doorloopt.

    Absoluut op de klok toetsen zou een machine die een week uit stond als
    storing melden. De vergelijking loopt daarom tegen de jóngste scheduler-run:
    staat alles stil, dan is er niets aan de hand met déze job.
    """
    from backend.domains.iris import integrity
    b = integrity._check_job_stil_terwijl_de_rest_draait()
    assert not [x for x in b if "__baseline__" in x.detail], (
        "de nulmeting van een verse installatie is geen taak en zou de kaart "
        "voor altijd openhouden"
    )
    for x in b:
        assert x.subject.startswith("job:")


def test_voortgang_telt_uit_de_taken_en_niet_uit_het_plan():
    """`task_count` is het plan, `goal_tasks` is de wereld.

    Bij vijf doelen stond `task_count` op 14 terwijl er 28 taakrijen waren. Een
    balk die op de plánwaarde deelt geeft "26/14" — en dat is precies hoe de
    dubbele uitvoering elf dagen als weergavefout gelezen kon worden.
    """
    goal_id = _nieuw_doel("WeAreImpact", "telling")
    with get_conn() as conn:
        conn.execute("UPDATE goals SET task_count = 1, completed_tasks = 2 WHERE id = ?",
                     (goal_id,))
    for n in (1, 2):
        _nieuwe_taak(goal_id, f"Taak {n}", "resultaat")
    doel = next(g for g in goal_service.list_goals(limit=50, project="WeAreImpact")
                if g["id"] == goal_id)
    assert doel["tasks_actual"] == 2, "de noemer komt uit goal_tasks"
    assert doel["completed_actual"] == 2
    assert doel["task_count"] == 1, "de plánwaarde blijft staan — het verschil is het signaal"


def test_doel_zonder_taken_heet_niet_voltooid():
    """Een doel dat nooit een taak kreeg, heeft niets gedaan.

    Twee doelen van Bewaard voor Jou staan op 'completed' met nul fases en nul
    taken ('SEO-blitz: gap-keyword content + kennisbank-herstel', 8 jul 2026).
    Er is niets gepland en niets uitgevoerd, en tóch telt het mee als afgerond
    werk — inclusief het dempen van dashboard-alerts via `_goal_addresses`.
    """
    from backend.domains.iris import integrity
    goal_id = _nieuw_doel("WeAreImpact", "leeg doel", status="completed")
    gevonden = [b for b in integrity._check_doel_voltooid_zonder_taken()
                if b.subject == f"goal:{goal_id}"]
    assert len(gevonden) == 1
    # Een lopend of gestrand doel zonder taken is iets anders: dat is werk in
    # uitvoering of een mislukte planning, geen valse voltooiing.
    ander = _nieuw_doel("WeAreImpact", "leeg maar partial", status="partial")
    assert not [b for b in integrity._check_doel_voltooid_zonder_taken()
                if b.subject == f"goal:{ander}"]
