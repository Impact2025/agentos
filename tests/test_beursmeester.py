"""De Beursmeester rekent zichzelf niet rijk.

Elk van deze tests legt één manier vast waarop een beleggingsagent zichzelf
gunstiger voorstelt dan hij is. Ze zijn niet bedacht na een incident maar
vooraf, omdat de vertekeningen in dit vak bekend zijn en het domein er van dag
één tegen bestand moest zijn:

  * een fill op de koers die het model zág, is gratis vooruitkijken;
  * kosten en slippage weglaten maakt van een backtest een reclamefolder;
  * een dollarpositie bij een eurosaldo optellen geeft een NAV die er
    geloofwaardig uitziet en fout is — de gevaarlijkste soort;
  * gesloten verliezers uit het rendement laten is de klassieke survivorship-
    truc;
  * een voorstel zonder stop heeft geen grootte, alleen een gok;
  * een voorstel zonder backtest is een mening met een koersdoel eraan;
  * en een rendement zonder benchmark zegt niets.
"""
from datetime import date, timedelta

import pytest

from backend.domains.invest import analyst, broker, features, history, portfolio, risk, service
from backend.shared.database import get_conn


PF = "test-portefeuille"


@pytest.fixture(autouse=True)
def _schoon():
    def _leeg():
        with get_conn() as conn:
            for tabel in ("invest_trades", "invest_positions", "invest_proposals",
                          "invest_runs", "invest_nav", "invest_portfolio", "market_history"):
                conn.execute(f"DELETE FROM {tabel}")
            conn.execute("DELETE FROM agent_predictions WHERE agent = 'invest'")
            conn.execute("DELETE FROM agent_lessons WHERE agent = 'invest'")
    _leeg()
    yield
    _leeg()


def _koersen(symbol: str, sluitingen: list, *, start: str = "2026-07-01",
             hoog_extra: float = 1.0, laag_extra: float = 1.0) -> None:
    """Zet een reeks dagkoersen klaar, één handelsdag per element."""
    dag = date.fromisoformat(start)
    rijen = []
    for slot in sluitingen:
        rijen.append((symbol, dag.isoformat(), slot, slot + hoog_extra, slot - laag_extra,
                      slot, 1000.0, "EUR", "2026-08-02"))
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


# ── De fill ────────────────────────────────────────────────────────────────

def test_fill_gebeurt_op_de_volgende_koers_niet_op_de_koers_die_het_model_zag():
    """Een besluit op de slotkoers van dag X wordt op zijn vroegst op dag X+1
    uitgevoerd. Vullen op de prijs uit de analyse is vooruitkijken."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0, 110.0], start="2026-07-01")

    trade = broker.voer_uit(
        portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
        besluit_dag="2026-07-01", ref_price=100.0, stop=90.0,
    )
    assert trade["fill_dag"] == "2026-07-02"
    # 110 (de volgende koers) plus slippage — nooit de 100 uit de these.
    assert trade["price"] > 110.0
    assert trade["ref_price"] == 100.0


def test_order_zonder_volgende_koers_is_niet_uitgevoerd():
    """Geen volgende handelsdag = de order is er niet. Bewust een exception:
    stilzwijgend op de laatste koers vullen zou een positie tonen die in het
    echt niet bestaat."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0], start="2026-07-01")

    with pytest.raises(broker.NietUitgevoerd):
        broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
                        besluit_dag="2026-07-01", ref_price=100.0)


def test_kosten_en_slippage_gaan_van_de_kas_af():
    """Een papieren portefeuille die gratis handelt, verslaat elke index."""
    _portefeuille(cash=10000.0)
    _koersen("IWDA.AS", [100.0, 100.0], start="2026-07-01")

    trade = broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
                            besluit_dag="2026-07-01", ref_price=100.0, stop=90.0)
    pf = portfolio.get(PF)
    assert trade["fee"] > 0
    assert trade["price"] > 100.0, "slippage hoort tégen je te werken bij kopen"
    # Kas = 10000 - (fill * qty) - kosten. Nooit precies 9000.
    assert pf["cash"] < 9000.0


def test_slippage_werkt_bij_verkopen_de_andere_kant_op():
    _portefeuille()
    _koersen("IWDA.AS", [100.0, 100.0, 100.0], start="2026-07-01")
    broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
                    besluit_dag="2026-07-01", ref_price=100.0, stop=90.0)
    verkoop = broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="sell", qty=10,
                              besluit_dag="2026-07-02", ref_price=100.0)
    assert verkoop["price"] < 100.0


def test_shorten_kan_niet():
    _portefeuille()
    _koersen("IWDA.AS", [100.0, 100.0], start="2026-07-01")
    with pytest.raises(broker.NietUitgevoerd):
        broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="sell", qty=5,
                        besluit_dag="2026-07-01", ref_price=100.0)


def test_index_is_geen_instrument():
    """^GSPC is een thermometer, geen positie."""
    _portefeuille()
    _koersen("^GSPC", [5000.0, 5100.0], start="2026-07-01")
    with pytest.raises(broker.NietUitgevoerd):
        broker.voer_uit(portfolio_id=PF, symbol="^GSPC", side="buy", qty=1,
                        besluit_dag="2026-07-01", ref_price=5000.0)


# ── Waardering ─────────────────────────────────────────────────────────────

def test_positie_in_vreemde_valuta_zonder_wisselkoers_is_onwaardeerbaar():
    """Dollars bij euro's optellen geeft een NAV die er geloofwaardig uitziet
    en fout is. Liever geen getal dan een verkeerd getal."""
    _portefeuille()
    _koersen("AAPL", [200.0, 200.0], start="2026-07-01")   # AAPL noteert in USD
    broker_fout = None
    try:
        broker.voer_uit(portfolio_id=PF, symbol="AAPL", side="buy", qty=5,
                        besluit_dag="2026-07-01", ref_price=200.0, stop=180.0)
    except broker.NietUitgevoerd as e:
        broker_fout = str(e)
    # Zonder EURUSD=X in de historie kán de order niet eens worden uitgevoerd:
    # de kas is dan niet correct bij te werken.
    assert broker_fout and "wisselkoers" in broker_fout


def test_nav_wordt_niet_vastgelegd_als_hij_onvolledig_is():
    """Eén verkeerd punt vervuilt de hele koerslijn, en die reeks is later het
    bewijsmateriaal."""
    _portefeuille()
    _koersen("EURUSD=X", [1.10, 1.10], start="2026-07-01")
    _koersen("AAPL", [200.0, 200.0], start="2026-07-01")
    broker.voer_uit(portfolio_id=PF, symbol="AAPL", side="buy", qty=5,
                    besluit_dag="2026-07-01", ref_price=200.0, stop=180.0)
    # Nu de wisselkoers weghalen: de positie wordt onwaardeerbaar.
    with get_conn() as conn:
        conn.execute("DELETE FROM market_history WHERE symbol = 'EURUSD=X'")

    snap = portfolio.snapshot(PF)
    assert not snap["volledig"]
    assert portfolio.leg_nav_vast(PF) is None


def test_gesloten_verliezers_tellen_mee_in_het_rendement():
    """De klassieke survivorship-truc: alleen open posities tonen. Een gesloten
    verlies moet in de NAV zichtbaar blijven via de kas."""
    _portefeuille(cash=10000.0)
    _koersen("IWDA.AS", [100.0, 100.0, 50.0, 50.0], start="2026-07-01")

    broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=50,
                    besluit_dag="2026-07-01", ref_price=100.0, stop=90.0)
    broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="sell", qty=50,
                    besluit_dag="2026-07-02", ref_price=50.0, reden="stop")

    r = portfolio.rendement(PF)
    assert r["rendement_pct"] is not None
    assert r["rendement_pct"] < -20, "het gerealiseerde verlies moet in het rendement zitten"
    gesloten = [p for p in portfolio.posities(PF, alleen_open=False) if p["status"] == "closed"]
    assert len(gesloten) == 1 and gesloten[0]["realized_pnl"] < 0


def test_rendement_zwijgt_zonder_benchmark():
    """Zonder vergelijking is +4% geen prestatie maar een getal."""
    _portefeuille()
    with get_conn() as conn:
        conn.execute("UPDATE invest_portfolio SET benchmark_start_price = NULL WHERE id = ?", (PF,))
    r = portfolio.rendement(PF)
    assert r["benchmark_pct"] is None
    assert r["alpha_pct"] is None
    assert "benchmark" in r["onvolledig_reden"].lower()


# ── Risico ─────────────────────────────────────────────────────────────────

def test_positiegrootte_volgt_uit_de_stopafstand():
    """Een wijde stop hoort een kleinere positie te geven, niet dezelfde."""
    _portefeuille(cash=10000.0)
    _koersen("IWDA.AS", [100.0] * 30, start="2026-07-01")
    portfolio.leg_nav_vast(PF, "2026-07-30")

    strak = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                            "ref_price": 100.0, "stop": 98.0}, PF)
    wijd = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                           "ref_price": 100.0, "stop": 90.0}, PF)
    assert strak["toegestaan"] and wijd["toegestaan"]
    assert strak["qty"] > wijd["qty"]


def test_voorstel_zonder_stop_wordt_geweigerd():
    """Zonder stop bestaat er geen grootte, alleen een gok."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 30, start="2026-07-01")
    portfolio.leg_nav_vast(PF, "2026-07-30")
    oordeel = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                              "ref_price": 100.0, "stop": None}, PF)
    assert not oordeel["toegestaan"]


def test_positie_wordt_afgetopt_op_de_maximale_weging():
    """Een zeer strakke stop zou anders een positie van meerdere keren de NAV
    opleveren — wiskundig correct, praktisch waanzin."""
    _portefeuille(cash=10000.0)
    _koersen("IWDA.AS", [100.0] * 30, start="2026-07-01")
    portfolio.leg_nav_vast(PF, "2026-07-30")
    oordeel = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                              "ref_price": 100.0, "stop": 99.9}, PF)
    assert oordeel["toegestaan"]
    assert oordeel["waarde_eur"] <= 10000 * 0.15 + 1


def test_afkoelperiode_na_een_stop_out():
    """Direct terugkopen na een stop is één verkeerde these twee keer betalen."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 30, start="2026-07-01")
    portfolio.leg_nav_vast(PF, "2026-07-30")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_positions (id, portfolio_id, symbol, asset_class, qty, avg_price, "
            "status, opened_on, closed_on, close_reason) VALUES "
            "('p1', ?, 'IWDA.AS', 'etf', 0, 100.0, 'closed', '2026-07-01', ?, 'stop')",
            (PF, date.today().isoformat()))

    oordeel = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                              "ref_price": 100.0, "stop": 95.0}, PF)
    assert not oordeel["toegestaan"]
    assert "afkoel" in oordeel["reden"]


def test_handelsstop_blokkeert_alles():
    _portefeuille()
    risk.zet_handelsstop(PF, 5, "test")
    status = risk.portefeuille_status(PF)
    assert not status["mag_handelen"]
    oordeel = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                              "ref_price": 100.0, "stop": 95.0}, PF)
    assert not oordeel["toegestaan"]


def test_grenzen_zijn_niet_te_toetsen_zonder_volledige_nav():
    """Sizen op een verzonnen noemer is erger dan niet sizen."""
    _portefeuille()
    _koersen("EURUSD=X", [1.10] * 5, start="2026-07-01")
    _koersen("AAPL", [200.0] * 5, start="2026-07-01")
    broker.voer_uit(portfolio_id=PF, symbol="AAPL", side="buy", qty=5,
                    besluit_dag="2026-07-01", ref_price=200.0, stop=180.0)
    with get_conn() as conn:
        conn.execute("DELETE FROM market_history WHERE symbol = 'EURUSD=X'")

    oordeel = risk.beoordeel({"symbol": "IWDA.AS", "side": "buy",
                              "ref_price": 100.0, "stop": 95.0}, PF)
    assert not oordeel["toegestaan"]
    assert "NAV" in oordeel["reden"]


# ── Stops worden echt uitgevoerd ───────────────────────────────────────────

def test_stop_wordt_geraakt_op_de_dag_low_niet_op_de_slotkoers():
    """Een stop die intraday geraakt werd, is geraakt — ook als de koers
    herstelde. Andersom rekenen stelt het risico stelselmatig te laag voor."""
    _portefeuille()
    # Slotkoersen blijven hoog, maar de low van de laatste dag duikt onder de stop.
    _koersen("IWDA.AS", [100.0, 100.0, 100.0], start="2026-07-01", laag_extra=1.0)
    broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
                    besluit_dag="2026-07-01", ref_price=100.0, stop=99.5)
    with get_conn() as conn:
        conn.execute("UPDATE market_history SET low = 98.0 WHERE symbol = 'IWDA.AS' "
                     "AND date = '2026-07-03'")

    uitkomst = broker.controleer_stops(PF)
    assert len(uitkomst["gesloten"]) == 1
    assert uitkomst["gesloten"][0]["reden"] == "stop"
    assert portfolio.positie(PF, "IWDA.AS") is None


# ── De analist en zijn contract ────────────────────────────────────────────

def _rauw(**overrides) -> dict:
    voorstel = {
        "symbol": "IWDA.AS", "side": "buy", "these": "test",
        "stop": 95.0, "target": 115.0, "horizon_dagen": 20,
        "invalidatie": "onder 95", "confidence": "midden",
        "backtest": "backtest.py", "backtest_uitkomst": "42 waarnemingen",
    }
    voorstel.update(overrides)
    return {"marktbeeld": "test", "voorstellen": [voorstel], "afgevallen": []}


def test_voorstel_zonder_backtest_artefact_wordt_geweigerd(tmp_path):
    """De harde eis. Een naam die naar niets verwijst is geen bewijs — anders
    is "backtest": "ja" genoeg om de gate te passeren."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 5, start=(date.today() - timedelta(days=4)).isoformat())

    goed, fout = analyst.valideer(_rauw(), tmp_path, PF)
    assert goed == []
    assert "backtest" in fout[0]["reden"]

    (tmp_path / "backtest.py").write_text("# echt bestand", encoding="utf-8")
    goed, fout = analyst.valideer(_rauw(), tmp_path, PF)
    assert len(goed) == 1 and not fout


def test_verzonnen_ticker_wordt_geweigerd(tmp_path):
    """Een taalmodel noemt vroeg of laat een symbool dat niet bestaat. De
    prompt vraagt het niet te doen; deze toets handhaaft het."""
    _portefeuille()
    (tmp_path / "backtest.py").write_text("#", encoding="utf-8")
    goed, fout = analyst.valideer(_rauw(symbol="MOONSHOT.XX"), tmp_path, PF)
    assert goed == [] and "universum" in fout[0]["reden"]


def test_voorstel_op_verouderde_koers_wordt_geweigerd(tmp_path):
    """Een these op koersen van vorige maand leest precies zo overtuigend."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 5, start="2026-06-01")   # ruim over datum
    (tmp_path / "backtest.py").write_text("#", encoding="utf-8")
    goed, fout = analyst.valideer(_rauw(), tmp_path, PF)
    assert goed == [] and "te oud" in fout[0]["reden"]


def test_stop_aan_de_verkeerde_kant_wordt_geweigerd(tmp_path):
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 5, start=(date.today() - timedelta(days=4)).isoformat())
    (tmp_path / "backtest.py").write_text("#", encoding="utf-8")
    goed, fout = analyst.valideer(_rauw(stop=105.0), tmp_path, PF)
    assert goed == [] and "boven de koers" in fout[0]["reden"]


def test_geweigerde_voorstellen_verdwijnen_niet_stil(tmp_path):
    """Zonder de weigeringslijst leer je nooit dat de analist structureel
    hetzelfde fout doet."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 5, start=(date.today() - timedelta(days=4)).isoformat())
    rauw = {"voorstellen": [
        {"symbol": "NIETBESTAAND", "side": "buy", "stop": 1, "backtest": "x"},
        {"symbol": "^AEX", "side": "buy", "stop": 1, "backtest": "x"},
    ]}
    goed, fout = analyst.valideer(rauw, tmp_path, PF)
    assert goed == []
    assert len(fout) == 2
    assert all(f["reden"] for f in fout)


# ── De gate ────────────────────────────────────────────────────────────────

def test_afwijzen_sluit_ook_de_voorspelling():
    """Een voorspelling laten doorlopen op een afgewezen idee meet iets anders
    dan wat de agent daadwerkelijk deed."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 30, start="2026-07-01")
    portfolio.leg_nav_vast(PF, "2026-07-30")

    voorstel = {"symbol": "IWDA.AS", "side": "buy", "asset_class": "etf",
                "ref_price": 100.0, "ref_date": "2026-07-30", "stop": 95.0,
                "target": 115.0, "horizon_days": 20, "thesis": "t",
                "invalidation": "i", "confidence": "midden", "backtest_ref": "x"}
    oordeel = risk.beoordeel(voorstel, PF)
    pid = service._bewaar_voorstel(PF, "run1", voorstel, oordeel, "claude_code", "")

    opgeslagen = service.voorstel(pid)
    assert opgeslagen["prediction_id"]

    service.wijs_af(pid, "niet overtuigd")
    with get_conn() as conn:
        status = conn.execute("SELECT status FROM agent_predictions WHERE id = ?",
                              (opgeslagen["prediction_id"],)).fetchone()["status"]
    assert status == "unclear"


def test_baseline_van_de_voorspelling_komt_uit_de_koershistorie():
    """Niet uit de tekst van het model — anders kan het model achteraf geen
    ongelijk krijgen, en is de leerlus een complimentenmachine."""
    _portefeuille()
    _koersen("IWDA.AS", [100.0] * 30, start="2026-07-01")
    portfolio.leg_nav_vast(PF, "2026-07-30")

    voorstel = {"symbol": "IWDA.AS", "side": "buy", "asset_class": "etf",
                "ref_price": 100.0, "ref_date": "2026-07-30", "stop": 95.0,
                "target": None, "horizon_days": 10, "thesis": "", "invalidation": "",
                "confidence": "", "backtest_ref": "x"}
    pid = service._bewaar_voorstel(PF, "run1", voorstel, risk.beoordeel(voorstel, PF),
                                   "claude_code", "")
    with get_conn() as conn:
        pred = conn.execute(
            "SELECT baseline, metric, context FROM agent_predictions WHERE id = "
            "(SELECT prediction_id FROM invest_proposals WHERE id = ?)", (pid,)).fetchone()
    assert pred["baseline"] == 100.0
    assert pred["metric"] == "close" and pred["context"] == "IWDA.AS"


def test_resolver_geeft_none_zonder_koers():
    """Niet meetbaar is 'unclear', geen misser."""
    assert service._resolver("close", "BESTAATNIET") is None
    assert service._resolver("iets_anders", "IWDA.AS") is None


# ── Deterministische laag ──────────────────────────────────────────────────

def test_regime_zegt_onbekend_bij_lege_historie():
    """'Neutraal' en 'we weten het niet' door elkaar halen is hoe een lege
    datafeed als rustige markt op het scherm komt."""
    assert features.regime()["regime"] == "onbekend"


def test_kenmerken_geven_none_bij_te_weinig_historie():
    _koersen("IWDA.AS", [100.0, 101.0, 102.0], start="2026-07-01")
    k = features.kenmerken("IWDA.AS")
    assert k["sma200"] is None and k["rsi14"] is None
    assert k["koers"] == 102.0


def test_verouderd_verschilt_per_assetklasse():
    """Crypto handelt in het weekend; een ETF niet. Eén drempel voor allebei
    geeft óf vals alarm op maandag óf een blinde vlek."""
    drie_dagen = (date.today() - timedelta(days=3)).isoformat()
    _koersen("IWDA.AS", [100.0], start=drie_dagen)
    _koersen("BTC-EUR", [50000.0], start=drie_dagen)
    assert not history.is_verouderd("IWDA.AS")   # grens 4 dagen
    assert history.is_verouderd("BTC-EUR")       # grens 2 dagen


# ── Invarianten ────────────────────────────────────────────────────────────

def test_invariant_vindt_positie_zonder_stop():
    from backend.domains.iris import integrity
    _portefeuille()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_positions (id, portfolio_id, symbol, qty, avg_price, "
            "status, opened_on) VALUES ('p9', ?, 'IWDA.AS', 10, 100.0, 'open', '2026-07-01')",
            (PF,))
    bevindingen = integrity.invariant("positie_zonder_stop").check()
    assert any("IWDA.AS" in b.detail for b in bevindingen)


def test_invariant_vindt_kasafwijking():
    """Twee werelden vergelijken: het saldo tegen het grootboek."""
    from backend.domains.iris import integrity
    _portefeuille(cash=10000.0)
    with get_conn() as conn:
        conn.execute("UPDATE invest_portfolio SET cash = 12345 WHERE id = ?", (PF,))
    bevindingen = integrity.invariant("kas_wijkt_af_van_grootboek").check()
    assert any("12345" in b.detail for b in bevindingen)


def test_invariant_kas_zwijgt_als_het_klopt():
    """Een invariant die altijd afgaat, wordt weggeklikt."""
    from backend.domains.iris import integrity
    _portefeuille(cash=10000.0)
    _koersen("IWDA.AS", [100.0, 100.0], start="2026-07-01")
    broker.voer_uit(portfolio_id=PF, symbol="IWDA.AS", side="buy", qty=10,
                    besluit_dag="2026-07-01", ref_price=100.0, stop=90.0)
    assert integrity.invariant("kas_wijkt_af_van_grootboek").check() == []


def test_alle_invest_invarianten_draaien_zonder_data():
    """Een verse installatie mag geen enkele toets laten crashen; een blinde
    toets die zwijgt is precies het probleem dat de audit moet vangen."""
    from backend.domains.iris import integrity
    for key in ("positie_zonder_stop", "koers_verouderd", "kas_wijkt_af_van_grootboek",
                "belegging_niet_afgerekend", "rendement_zonder_benchmark",
                "datafeed_stil", "voorstel_zonder_backtest"):
        inv = integrity.invariant(key)
        assert inv is not None, f"invariant '{key}' staat niet in het register"
        assert isinstance(inv.check(), list)
        assert inv.incident, f"invariant '{key}' zonder incident-veld"


# ── Alleen voltooide handelsdagen ──────────────────────────────────────────

def test_bar_van_vandaag_wordt_niet_opgeslagen(monkeypatch):
    """yfinance geeft tijdens beursuren een rij voor vandáág terug met de koers
    van dít moment in de kolom `Close`. Die ziet er in de tabel precies zo uit
    als een slotkoers en is het niet — en alles hieronder (200-daagse, ATR, de
    fill van `close_na`, de baseline van elke voorspelling) is op slotkoersen
    gedefinieerd. Ontdekt 4 aug 2026: de ochtendsync schreef een bar van 09:20
    weg als dagslot."""
    class _NepRij(dict):
        def get(self, k, d=None):  # pragma: no cover - triviale adapter
            return dict.get(self, k, d)

    gisteren = (date.today() - timedelta(days=1)).isoformat()
    vandaag = date.today().isoformat()

    class _NepFrame:
        empty = False
        def iterrows(self):
            for dag in (gisteren, vandaag):
                yield dag, _NepRij(Open=10.0, High=11.0, Low=9.0, Close=10.5, Volume=100)

    class _NepTicker:
        fast_info = {"currency": "EUR"}
        def history(self, **kw):
            return _NepFrame()

    import sys, types
    nep = types.ModuleType("yfinance")
    nep.Ticker = lambda s: _NepTicker()
    monkeypatch.setitem(sys.modules, "yfinance", nep)

    rijen = history._haal_symbool("IWDA.AS", "1mo")
    dagen = [r[0] for r in rijen]
    assert gisteren in dagen
    assert vandaag not in dagen, "een lopende handelsdag hoort geen slotkoers te worden"


# ── Terugval is zichtbaar ──────────────────────────────────────────────────

def test_bereikte_abonnementslimiet_wordt_als_limiet_herkend():
    """De CLI meldde op 3 aug 2026 "You've hit your monthly spend limit"; dat
    viel door elk signaal heen en werd een gewone storing, met de nutteloze
    suggestie het nog eens te proberen."""
    from backend.shared import claude_code
    for tekst in ("You've hit your monthly spend limit · raise it at claude.ai/settings",
                  "Claude usage limit reached", "rate limit exceeded"):
        assert any(sig in tekst.lower() for sig in claude_code._LIMIET_SIGNALEN), tekst
    assert not any(sig in "connection reset by peer" for sig in claude_code._LIMIET_SIGNALEN)


def test_terugval_ronde_meldt_zich_bij_een_bereikte_limiet():
    """Een terugval-ronde levert per definitie nul voorstellen op. In de cijfers
    ziet dat er precies zo uit als "de analist vond vandaag niets", en dat is
    een heel ander bericht."""
    with get_conn() as conn:
        conn.execute("DELETE FROM activity_log WHERE action = 'denkwerk_uitgevallen'")

    service._meld_terugval({
        "denkwerk": "terugval", "limiet": True,
        "reden": "You've hit your monthly spend limit",
        "werkmap": "/tmp/x",
    })
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT status, next_step FROM activity_log WHERE action = 'denkwerk_uitgevallen'"
        ).fetchall()
    assert len(rijen) == 1
    assert rijen[0]["status"] == "error"
    assert "limiet" in rijen[0]["next_step"].lower()

    # Tweede ronde dezelfde dag: één kaart per dag, geen stapel.
    service._meld_terugval({"denkwerk": "terugval", "limiet": True,
                            "reden": "spend limit", "werkmap": ""})
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) n FROM activity_log WHERE action = 'denkwerk_uitgevallen'"
        ).fetchone()["n"]
    assert n == 1
    with get_conn() as conn:
        conn.execute("DELETE FROM activity_log WHERE action = 'denkwerk_uitgevallen'")


def test_losse_blip_escaleert_niet_meteen():
    """Een netwerkhapering gaat vanzelf over; die hoort geen inbox-item te zijn
    vóór de reeks vol is (shared/failures.py)."""
    from backend.shared.database import get_conn as _gc
    with _gc() as conn:
        conn.execute("DELETE FROM activity_log WHERE action = 'denkwerk_uitgevallen'")
        conn.execute("DELETE FROM agent_failure_streaks WHERE key = 'invest_denkwerk'")

    service._meld_terugval({"denkwerk": "terugval", "limiet": False,
                            "reden": "connection reset", "werkmap": ""})
    with _gc() as conn:
        n = conn.execute(
            "SELECT COUNT(*) n FROM activity_log WHERE action = 'denkwerk_uitgevallen'"
        ).fetchone()["n"]
    assert n == 0, "één blip is geen storing"
    with _gc() as conn:
        conn.execute("DELETE FROM agent_failure_streaks WHERE key = 'invest_denkwerk'")
