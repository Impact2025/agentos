"""Het dashboard mag de portefeuille niet mooier voorstellen dan hij is.

`test_beursmeester.py` bewaakt de handel; dit bestand bewaakt de *rapportage*
erover. Dat is een eigen faalmodus, en een verraderlijke: de trades kunnen
kloppen terwijl het beeld erboven vleit. Vier manieren waarop dat gebeurt, elk
hieronder vastgelegd:

  * een statistiek zonder n — 100% trefkans over twee posities leest als bewijs;
  * een onmeetbaar cijfer dat als 0 wordt getoond, want 0 is een oordeel
    ("levert niets op") en onmeetbaar is een feit ("we weten het niet");
  * een risicomaat over een reeks met gaten, terwijl juist de dagen die
    ontbreken de dagen zijn waarop iets niet klopte;
  * een positie zonder stop die als "geen risico" in de sommatie verdwijnt.
"""
from datetime import date, timedelta

import pytest

from backend.domains.invest import analytics, broker, portfolio
from backend.shared.database import get_conn

PF = "test-dashboard"


@pytest.fixture(autouse=True)
def _schoon():
    def _leeg():
        with get_conn() as conn:
            for tabel in ("invest_trades", "invest_positions", "invest_proposals",
                          "invest_runs", "invest_nav", "invest_portfolio", "market_history"):
                conn.execute(f"DELETE FROM {tabel}")
            conn.execute("DELETE FROM agent_predictions WHERE agent = 'invest'")
    _leeg()
    yield
    _leeg()


def _koersen(symbol: str, sluitingen: list, *, start: str = "2026-07-01",
             valuta: str = "EUR") -> None:
    dag = date.fromisoformat(start)
    rijen = []
    for slot in sluitingen:
        rijen.append((symbol, dag.isoformat(), slot, slot + 1, slot - 1, slot, 1000.0,
                      valuta, "2026-08-02"))
        dag += timedelta(days=1)
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO market_history "
            "(symbol, date, open, high, low, close, volume, currency, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rijen)


def _portefeuille(cash: float = 10000.0) -> dict:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_portfolio (id, name, mode, base_currency, start_capital, cash, "
            "benchmark_symbol, benchmark_start_price, started_on, created_at) "
            "VALUES (?, 'Test', 'paper', 'EUR', ?, ?, 'IWDA.AS', 100.0, '2026-07-01', '2026-07-01')",
            (PF, cash, cash))
    return portfolio.get(PF)


def _handel(symbol: str, *, koersen: list, koop_dag: str, verkoop_dag: str,
            qty: float = 10, stop: float = 90.0, reden: str = "target") -> None:
    """Koop en verkoop één positie volledig, langs de echte broker — inclusief
    kosten en slippage, want een statistiek over gratis trades is fictie."""
    _koersen(symbol, koersen)
    broker.voer_uit(portfolio_id=PF, symbol=symbol, side="buy", qty=qty,
                    besluit_dag=koop_dag, ref_price=koersen[0], stop=stop, target=stop * 1.5)
    broker.voer_uit(portfolio_id=PF, symbol=symbol, side="sell", qty=qty,
                    besluit_dag=verkoop_dag, ref_price=koersen[-1], reden=reden)


# ── Statistiek draagt haar n ───────────────────────────────────────────────

def test_statistiek_zonder_afgesloten_posities_geeft_geen_cijfers():
    """Nul afgesloten posities is geen 0% trefkans maar 'nog niets te zeggen'.
    Een dashboard dat hier 0% toont, meldt een oordeel dat niemand heeft geveld."""
    _portefeuille()
    stat = analytics.handelsstatistiek(PF)
    assert stat["n"] == 0
    assert stat["zeggingskracht"] == "geen"
    assert "trefpercentage" not in stat
    assert stat["toelichting"]


def test_kleine_steekproef_heet_geen_bewijs():
    """Twee winnaars op rij is 100% — en zegt niets. Het cijfer mag er staan,
    maar altijd met het label dat vertelt hoe zwaar het weegt."""
    _portefeuille()
    _handel("IWDA.AS", koersen=[100.0, 100.0, 120.0], koop_dag="2026-07-01",
            verkoop_dag="2026-07-02")

    stat = analytics.handelsstatistiek(PF)
    assert stat["n"] == 1
    assert stat["trefpercentage"] == 100.0
    assert stat["zeggingskracht"] == "geen", "één trade mag nooit als betekenisvol tellen"


# ── Winst, verlies en de cijfers die er tussen zitten ──────────────────────

def test_verliezer_telt_mee_en_drukt_de_verwachting():
    """De klassieke survivorship-truc, nu op het niveau van de statistiek: laat
    je de verliezer weg, dan klopt elk cijfer op deze regel na — en is de
    verwachting per idee positief terwijl de portefeuille geld verliest."""
    _portefeuille()
    _handel("IWDA.AS", koersen=[100.0, 100.0, 130.0], koop_dag="2026-07-01",
            verkoop_dag="2026-07-02", qty=10, stop=90.0)
    _handel("ASML.AS", koersen=[100.0, 100.0, 50.0], koop_dag="2026-07-01",
            verkoop_dag="2026-07-02", qty=10, stop=90.0, reden="stop")

    stat = analytics.handelsstatistiek(PF)
    assert stat["n"] == 2
    assert stat["winnaars"] == 1 and stat["verliezers"] == 1
    assert stat["trefpercentage"] == 50.0
    assert stat["resultaat_eur"] < 0, "de verliezer is groter dan de winnaar"
    assert stat["verwachting_eur"] < 0
    assert stat["per_reden"]["stop"]["n"] == 1


def test_profit_factor_wordt_niet_oneindig_zonder_verliezers():
    """Delen door nul geeft 'oneindig', en dat leest als geniaal. Zonder
    verliezers bestaat de verhouding simpelweg nog niet."""
    _portefeuille()
    _handel("IWDA.AS", koersen=[100.0, 100.0, 120.0], koop_dag="2026-07-01",
            verkoop_dag="2026-07-02")

    stat = analytics.handelsstatistiek(PF)
    assert stat["profit_factor"] is None
    assert stat["payoff"] is None


def test_r_veelvoud_rekent_het_resultaat_af_tegen_het_geaccepteerde_risico():
    """Het enige getal waarin een grote en een kleine positie eerlijk naast
    elkaar staan: hoeveel keer het vooraf geaccepteerde verlies is dit geworden?"""
    _portefeuille()
    # Instap ~100, stop op 90 → geaccepteerd risico ≈ 10 per stuk. Uitstap ~120
    # → winst ≈ 20 per stuk ≈ 2R.
    _handel("IWDA.AS", koersen=[100.0, 100.0, 120.0], koop_dag="2026-07-01",
            verkoop_dag="2026-07-02", stop=90.0)

    regels = analytics.gesloten_resultaten(PF)["regels"]
    assert len(regels) == 1
    assert 1.5 < regels[0]["r_multiple"] < 2.5


def test_kosten_gaan_van_het_resultaat_af():
    """Een trackrecord zonder kosten is een reclamefolder. Het euro-resultaat
    hoort lager te liggen dan de kale koersbeweging."""
    _portefeuille()
    _handel("IWDA.AS", koersen=[100.0, 100.0, 120.0], koop_dag="2026-07-01",
            verkoop_dag="2026-07-02", qty=10)

    regel = analytics.gesloten_resultaten(PF)["regels"][0]
    assert regel["kosten_eur"] > 0
    bruto_pct = regel["resultaat_pct"]
    assert regel["resultaat_eur"] < bruto_pct / 100 * (regel["avg_price"] * regel["qty"])


def test_positie_zonder_wisselkoers_valt_buiten_de_statistiek_maar_niet_uit_beeld():
    """Onmeetbaar is niet nul. De positie mag niet als 0 euro meetellen — dat
    zou de verwachting per idee naar nul trekken — maar hij mag ook niet stil
    verdwijnen, want een verzwegen verliezer vleit net zo hard."""
    _portefeuille()
    _koersen("AAPL", [100.0, 100.0, 120.0])   # dollars, en geen EURUSD=X in de databank
    # De broker weigert een order zonder wisselkoers; deze positie is dus
    # gekocht toen de koers er nog was en pas daarna uit de databank verdwenen.
    # Grootboek en positie zetten we samen neer, want het percentage wordt uit
    # het grootboek gereconstrueerd.
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_positions (id, portfolio_id, symbol, asset_class, qty, avg_price, "
            "stop, target, horizon_days, thesis, proposal_id, status, opened_on, closed_on, "
            "close_reason, realized_pnl) VALUES ('p1', ?, 'AAPL', 'aandeel', 0, 100.0, 90.0, "
            "150.0, 14, '', '', 'closed', '2026-07-01', '2026-07-03', 'target', 200.0)",
            (PF,))
        for tid, side, prijs in [("t1", "buy", 100.0), ("t2", "sell", 120.0)]:
            conn.execute(
                "INSERT INTO invest_trades (id, portfolio_id, position_id, symbol, side, qty, "
                "price, ref_price, fee, reason, executed_on, created_at) VALUES (?, ?, 'p1', "
                "'AAPL', ?, 10, ?, ?, 1.0, 'entry', '2026-07-01', '2026-07-01')",
                (tid, PF, side, prijs, prijs))

    gesloten = analytics.gesloten_resultaten(PF)
    assert len(gesloten["regels"]) == 1, "de positie blijft zichtbaar"
    assert gesloten["regels"][0]["resultaat_eur"] is None, "geen verzonnen euro-bedrag"
    assert gesloten["regels"][0]["resultaat_pct"] is not None, "procenten zijn valuta-vrij"
    assert gesloten["onmeetbaar"], "en de reden staat erbij"

    stat = analytics.handelsstatistiek(PF)
    assert stat["n"] == 0
    assert stat["n_onmeetbaar"] == 1


# ── Open risico ────────────────────────────────────────────────────────────

def test_open_risico_telt_op_wat_alle_stops_samen_kosten():
    """De waarde van de portefeuille zegt niets over zijn kwetsbaarheid. Dit
    cijfer wel: wat kost het als élke stop vandaag raakt?"""
    _portefeuille()
    _koersen("IWDA.AS", [100.0, 100.0, 100.0])
    broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
                    besluit_dag="2026-07-01", ref_price=100.0, stop=90.0)

    risico = analytics.open_risico(PF)
    assert risico["posities"][0]["risico_eur"] == pytest.approx(100.0, abs=5.0)
    assert risico["risico_eur"] == pytest.approx(100.0, abs=5.0)
    assert risico["risico_pct_nav"] == pytest.approx(1.0, abs=0.2)


def test_positie_zonder_stop_draagt_geen_nul_maar_een_onbekend_risico():
    """Een positie zonder stop in de sommatie op 0 zetten, maakt de gevaarlijkste
    positie van de portefeuille onzichtbaar in precies het cijfer dat over
    gevaar gaat."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0, 100.0, 100.0])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_positions (id, portfolio_id, symbol, asset_class, qty, avg_price, "
            "stop, target, horizon_days, thesis, proposal_id, status, opened_on) "
            "VALUES ('p2', ?, 'IWDA.AS', 'etf', 10, 100.0, NULL, NULL, 14, '', '', 'open', "
            "'2026-07-01')", (PF,))

    risico = analytics.open_risico(PF)
    assert risico["posities"][0]["risico_eur"] is None
    assert risico["zonder_stop"] == ["IWDA.AS"]
    assert risico["volledig"] is False


def test_positie_zonder_stop_is_een_blokkerend_aandachtspunt():
    """En dat komt bovenaan het dashboard te staan, niet in kolom zeven van
    tabel vier."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0, 100.0, 100.0])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_positions (id, portfolio_id, symbol, asset_class, qty, avg_price, "
            "stop, target, horizon_days, thesis, proposal_id, status, opened_on) "
            "VALUES ('p3', ?, 'IWDA.AS', 'etf', 10, 100.0, NULL, NULL, 14, '', '', 'open', "
            "'2026-07-01')", (PF,))

    rapport = analytics.management_rapport(PF)
    blokkerend = [p for p in rapport["aandachtspunten"] if p["ernst"] == "blokkerend"]
    assert any("zonder stop" in p["tekst"] for p in blokkerend)


# ── Risicomaten over een gatenreeks ────────────────────────────────────────

def _nav_reeks(dagen: int, *, sla_over: set = frozenset()) -> None:
    """Zet een NAV-punt per handelsdag, met de benchmark ernaast."""
    dag = date.fromisoformat("2026-07-01")
    koersen = []
    with get_conn() as conn:
        for i in range(dagen):
            d = dag.isoformat()
            koersen.append(("IWDA.AS", d, 100.0, 101.0, 99.0, 100.0 + i, 1000.0, "EUR", "2026-08-02"))
            if i not in sla_over:
                conn.execute(
                    "INSERT OR REPLACE INTO invest_nav (portfolio_id, date, nav, cash, "
                    "positions_value, benchmark_price) VALUES (?, ?, ?, ?, 0, ?)",
                    (PF, d, 10000.0 + i * 10, 10000.0 + i * 10, 100.0 + i))
            dag += timedelta(days=1)
        conn.executemany(
            "INSERT OR REPLACE INTO market_history "
            "(symbol, date, open, high, low, close, volume, currency, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", koersen)


def test_risicomaten_zwijgen_bij_te_weinig_meetpunten():
    """Een volatiliteit over acht dagen is ruis met een decimaal erachter."""
    _portefeuille()
    _nav_reeks(8)
    lijn = analytics.koerslijn(PF)
    assert "volatiliteit_pct" not in lijn["risico"]
    assert "te weinig meetpunten" in lijn["risico"]["reden"]


def test_risicomaten_zwijgen_als_de_reeks_gaten_heeft():
    """`leg_nav_vast` slaat dagen met een onvolledige NAV over. Juist die dagen
    zijn de dagen waarop iets niet klopte, dus een terugval berekend zónder hen
    is stelselmatig te mooi. Liever een reden dan een gevleid cijfer."""
    _portefeuille()
    _nav_reeks(30, sla_over={5, 6, 7, 8, 9, 10})
    lijn = analytics.koerslijn(PF)
    assert lijn["gaten"] == 6
    assert "ontbrekende handelsdagen" in lijn["risico"]["reden"]


def test_risicomaten_verschijnen_bij_een_volledige_reeks():
    _portefeuille()
    _nav_reeks(30)
    lijn = analytics.koerslijn(PF)
    assert lijn["gaten"] == 0
    assert lijn["risico"]["volatiliteit_pct"] is not None
    assert lijn["risico"]["max_drawdown_pct"] == 0.0, "een stijgende lijn kent geen terugval"
    assert lijn["punten"][0]["nav_index"] == 100.0
    assert lijn["punten"][0]["bench_index"] == 100.0, "beide lijnen starten op 100"


# ── De trechter en de machine ──────────────────────────────────────────────

def test_trechter_toont_waarom_voorstellen_sneuvelen():
    """Zonder de verhouding tussen invoer en uitvoer weet je niet of de agent te
    weinig ideeën heeft of te strakke klemmen — tegengestelde ingrepen."""
    _portefeuille()
    with get_conn() as conn:
        for i, (status, note) in enumerate([
            ("pending_review", ""), ("geblokkeerd", "onvoldoende cash"),
            ("geblokkeerd", "onvoldoende cash"), ("rejected", ""), ("filled", ""),
        ]):
            conn.execute(
                "INSERT INTO invest_proposals (id, portfolio_id, symbol, side, ref_price, "
                "ref_date, status, risk_note, created_at) VALUES (?, ?, 'IWDA.AS', 'buy', 100.0, "
                "'2026-07-01', ?, ?, datetime('now'))",
                (f"v{i}", PF, status, note))

    f = analytics.voorstel_trechter(PF)
    assert f["voorgesteld"] == 5
    assert f["geblokkeerd"] == 2 and f["uitgevoerd"] == 1
    assert f["conversie_pct"] == 20.0
    assert f["blokkade_redenen"][0]["n"] == 2


def test_terugval_rondes_zijn_zichtbaar_als_uitgevallen_denkwerk():
    """Een ronde op de terugval kan niets backtesten en levert per definitie nul
    voorstellen op. In de cijfers ziet dat er precies zo uit als 'de analist vond
    niets', en dat is een heel ander bericht."""
    _portefeuille()
    with get_conn() as conn:
        for i, denkwerk in enumerate(["claude_code", "terugval", "terugval", "terugval"]):
            conn.execute(
                "INSERT INTO invest_runs (id, portfolio_id, run_date, denkwerk, status, "
                "proposals, created_at) VALUES (?, ?, ?, ?, 'ok', 0, datetime('now'))",
                (f"r{i}", PF, date.today().isoformat(), denkwerk))

    r = analytics.ronde_historie(PF)
    assert r["denkwerk_30d"]["terugval"] == 3
    assert r["echt_denkwerk_pct"] == 25.0

    rapport = analytics.management_rapport(PF)
    assert any("écht denkwerk" in p["tekst"] for p in rapport["aandachtspunten"])
