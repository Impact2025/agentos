"""De sky-scan moet aflopen, en eerlijk rondgaan.

Aanleiding (4 aug 2026). `scheduler_runs` noteerde voor `radar_sky_scan`
`last_run_at = 23 juli` terwijl de watchlist aantoonbaar wél was aangeraakt en
de job elke vier uur hoort te vuren. De job was niet stuk — hij was nooit
klaar: `_record_run` schrijft pas ná afloop (EVENT_JOB_EXECUTED), en 171
watches × (websearch + LLM-verrijking) passen niet in vier uur. APScheduler
slaat met `max_instances=1` elke volgende vuurbeurt over zolang de vorige
loopt, dus een job die nooit eindigt is een job die precies één keer draait.
Ondertussen bleven 3.375 signalen op 'new' staan.

Twee dingen liggen hier vast, en ze horen bij elkaar:

  * de scan is **begrensd** — hij loopt altijd af, dus de scheduler krijgt zijn
    afloop-event en de run-historie vertelt weer de waarheid;
  * de scan is **hervatbaar én eerlijk** — `list_watch` sorteert op
    `created_at`, en dat is precies fout voor een scan die niet altijd
    uitloopt: de kop komt élke ronde aan de beurt en de staart nooit. Er stonden
    5 RSS-feeds met een lege `last_scanned_at`, nog geen één keer gescand sinds
    hun aanmaak, terwijl de eerste zeven keywords al weken meeliepen.

Zonder het tweede is het eerste schadelijk: begrenzen zonder de volgorde te
repareren, betekent dat de staart voor altijd onbereikbaar wordt.
"""
import uuid

import pytest

from backend.domains.radar import service as radar_service
from backend.shared.database import get_conn


@pytest.fixture
def watches():
    """Maak actieve watches met een gegeven `last_scanned_at`."""
    from backend.domains.radar.models import ensure_schema
    ensure_schema()
    gemaakt = []

    def _zet(label, last_scanned_at):
        wid = f"w-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO radar_watchlist (id, project, label, type, value, active, "
                "last_scanned_at, created_at) VALUES (?, ?, ?, 'keyword', ?, 1, ?, "
                "datetime('now'))",
                (wid, "scanvenster", label, label, last_scanned_at))
        gemaakt.append(wid)
        return wid

    yield _zet
    with get_conn() as c:
        for wid in gemaakt:
            c.execute("DELETE FROM radar_watchlist WHERE id = ?", (wid,))


def _volgorde(svc):
    """De volgorde waarin de scan de watches zou aflopen."""
    ws = [w for w in svc.list_watch("scanvenster") if w["active"]]
    ws.sort(key=lambda w: (w.get("last_scanned_at") or ""))
    return [w["label"] for w in ws]


class TestEerlijkeVolgorde:

    def test_nooit_gescand_gaat_voor(self, watches):
        """Een lege `last_scanned_at` betekent 'nog nooit' — die hoort vooraan.

        Dit is het geval dat vijf RSS-feeds maandenlang onzichtbaar hield.
        """
        watches("recent-gescand", "2026-08-03T21:07:00")
        watches("nooit-gescand", "")
        watches("lang-geleden", "2026-07-01T06:00:00")

        assert _volgorde(radar_service.get_service())[0] == "nooit-gescand"

    def test_oudste_daarna(self, watches):
        watches("gisteren", "2026-08-03T00:00:00")
        watches("vorige-maand", "2026-07-01T00:00:00")
        watches("vandaag", "2026-08-04T00:00:00")

        assert _volgorde(radar_service.get_service()) == [
            "vorige-maand", "gisteren", "vandaag"]

    def test_de_kop_verhongert_de_staart_niet(self, watches):
        """Na een ronde schuift wie net gescand is naar achteren.

        Precies dít maakt begrenzen veilig: een afgebroken ronde verliest niets,
        want de volgende begint bij wie is blijven liggen.
        """
        svc = radar_service.get_service()
        for n in range(5):
            watches(f"watch-{n}", f"2026-07-0{n + 1}T00:00:00")

        eerste = _volgorde(svc)[0]
        assert eerste == "watch-0"

        # 'watch-0' is nu gescand — hij hoort achteraan aan te sluiten.
        with get_conn() as c:
            c.execute("UPDATE radar_watchlist SET last_scanned_at = '2026-08-04T12:00:00' "
                      "WHERE label = 'watch-0' AND project = 'scanvenster'")
        opnieuw = _volgorde(svc)
        assert opnieuw[0] == "watch-1"
        assert opnieuw[-1] == "watch-0"


class TestBudget:

    def test_budget_is_ruim_onder_het_scaninterval(self):
        """De scan moet af zijn vóór de volgende vuurt, anders begint de
        blokkade opnieuw. Het interval is vier uur."""
        assert 0 < radar_service._SCAN_BUDGET_SECONDS < 4 * 3600

    def test_budget_is_uit_te_zetten(self, monkeypatch):
        """Een handmatige scan via de UI mag wél volledig zijn."""
        monkeypatch.setattr(radar_service, "_SCAN_BUDGET_SECONDS", 0)
        assert not radar_service._SCAN_BUDGET_SECONDS
