"""Tests voor de AEO-autonomie-uitbreiding.

Deze tests draaien tegen een wegwerp-DB (conftest zet IMPACTOS_DB_PATH).
We mocken de agent-runner zodat we de pijplijn-logica testen zonder echte
LLM-backend, en testen de deterministic fallback los daarvan.
"""
import os
import pytest

# Zet autonomie-vlaggen expliciet voor de tests (voordat config geïmporteerd
# wordt). config.py leest ze één keer bij import, dus moet dit vóór de import.
os.environ.setdefault("AEO_AUTO_ATTACK", "1")
os.environ.setdefault("AEO_AUTO_MIN_SCORE", "75")
os.environ.setdefault("AEO_AUTO_MAX_PER_SCAN", "3")
os.environ.setdefault("HERMES_LOCAL_FALLBACK", "1")


@pytest.fixture()
def radar():
    from backend.domains.radar.service import RadarService
    return RadarService()


def _make_signal(radar, title, score, status="new", project="bijeen", match=80):
    from backend.shared.database import get_conn
    import json, uuid
    sig_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO radar_signals "
            "(id, watch_id, project, keyword, title, url, source, snippet, "
            "published_days_ago, signal_score, ai_hook, ai_angle, ai_titles, "
            "ai_match_score, status, obsidian_path, scanned_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sig_id, str(uuid.uuid4()), project, "vrijwilligers werven", title,
             "https://reddit.com/r/test/comments/" + sig_id + "/" + title,
             "reddit", "snippet", 2, score,
             "hook", "angle", json.dumps(["Titel A", "Titel B"]), match,
             status, "", "2026-07-08T00:00:00Z", "2026-07-08T00:00:00Z",
             "2026-07-08T00:00:00Z"),
        )
    return sig_id


def test_auto_aeo_selects_top_signals(radar):
    """Alleen 'new' signalen boven de drempel worden aangevallen, max N."""
    _make_signal(radar, "laagscore", 40)          # onder drempel
    _make_signal(radar, "middelscore", 78)        # boven drempel
    _make_signal(radar, "hoogscore1", 95)         # boven drempel
    _make_signal(radar, "hoogscore2", 90)         # boven drempel
    _make_signal(radar, "al-converted", 99, status="converted")

    # aeo_attack maakt taken aan — tel hoeveel er bijkomen.
    from backend.shared.database import get_conn
    with get_conn() as c:
        before = len(c.execute("SELECT id FROM tasks").fetchall())
    attacked = radar._auto_aeo_top_signals()
    with get_conn() as c:
        after = len(c.execute("SELECT id FROM tasks").fetchall())

    # 3 aangevallen (middelscore 78, hoogscore1 95, hoogscore2 90) × 3 kanalen
    # = 9 taken. "laagscore" (40) en "al-converted" (99) worden overgeslagen.
    assert "middelscore" in attacked
    assert "hoogscore1" in attacked
    assert "hoogscore2" in attacked
    assert "laagscore" not in attacked
    assert "al-converted" not in attacked
    assert after - before == 9  # 3 signalen × 3 kanalen


def test_auto_aeo_skipt_signaal_zonder_werkende_relevantie(radar):
    """Een hoge signal_score op louter versheid + keyword-boost bewijst geen
    topische fit. Zonder een WERKENDE ai_match_score (>= drempel) mag de agent
    niet autonoom aanvallen — anders glipt een off-topic stuk de Wachtrij in."""
    on_topic = _make_signal(radar, "goede-fit", 90, match=85)
    off_topic = _make_signal(radar, "hoog-maar-onbewezen", 95, match=-1)  # relevantie gefaald
    laag_match = _make_signal(radar, "hoog-lage-match", 92, match=20)     # expliciet irrelevant

    attacked = radar._auto_aeo_top_signals()
    assert "goede-fit" in attacked
    assert "hoog-maar-onbewezen" not in attacked   # match=-1 telt als niet-gehaald
    assert "hoog-lage-match" not in attacked        # match=20 < 60


def test_auto_aeo_hoge_match_alleen_redt_lage_score_niet(radar):
    """Een hoge match mag de score-drempel NIET overrulen: het match-model
    stempelt bijna alles hoog, dus een lage signal_score blijft blokkerend —
    anders keert off-topic ruis met een toevallig hoge match terug."""
    laag_vers = _make_signal(radar, "hoge-match-lage-score", 50, match=95)  # score < 66
    attacked = radar._auto_aeo_top_signals()
    assert "hoge-match-lage-score" not in attacked


def test_boost_matcht_ondanks_leestekens_en_niet_binnen_woorden():
    """Woordgrens-veilig: '®' mag de token niet breken, en 'ai' mag niet binnen
    'email'/'detail' matchen."""
    from backend.domains.radar import scorer
    kw = "algemeen"
    # 'lego® serious play®' moet de +14 lego-token alsnog pakken.
    met_symbool = scorer.compute_signal_score(
        "LEGO® Serious Play® workshop", "http://x", kw, 0, 0.0,
        project="weareimpact", snippet="een sessie")
    zonder = scorer.compute_signal_score(
        "Gewone workshop zonder trefwoorden", "http://x", kw, 0, 0.0,
        project="weareimpact", snippet="een sessie")
    assert met_symbool > zonder  # de boost sloeg aan ondanks de ®-tekens
    # 'ai' mag NIET binnen 'email'/'detail' matchen (geen valse boost).
    vals = scorer.compute_signal_score(
        "Stuur een email met detail over de campaign", "http://x", kw, 0, 0.0,
        project="weareimpact", snippet="geen relevante trefwoorden hier xyz")
    assert vals == zonder  # zelfde base, geen boost uit toevallige substrings


def test_auto_aeo_is_idempotent(radar):
    """Een tweede run valt terug op reeds-'converted' signalen."""
    _make_signal(radar, "uniek", 88)
    radar._auto_aeo_top_signals()
    # Na de eerste run is "uniek" → 'converted'. Tweede run mag niets nieuws doen.
    from backend.shared.database import get_conn
    with get_conn() as c:
        before = len(c.execute("SELECT id FROM tasks").fetchall())
    radar._auto_aeo_top_signals()
    with get_conn() as c:
        after = len(c.execute("SELECT id FROM tasks").fetchall())
    assert after == before


def test_boost_beloont_onderwerp_niet_het_keyword():
    """De high-value boost mag alleen aanslaan als het GEVONDEN stuk het
    onderwerp raakt (titel/snippet), niet omdat het keyword de magische woorden
    bevat. Een off-topic titel onder zo'n keyword hoort de boost te missen."""
    from backend.domains.radar import scorer
    kw = "lego serious play ai draagvlak"  # keyword bevat de boost-tokens
    off = scorer.compute_signal_score(
        title="Effects of an AI-VR flipped classroom on English achievement",
        url="https://nature.com/x", keyword=kw, published_days_ago=0,
        project="weareimpact", snippet="A study on VR and English test scores.")
    on = scorer.compute_signal_score(
        title="Lego Serious Play voor draagvlak in de zorg",
        url="https://example.nl/y", keyword=kw, published_days_ago=0,
        project="weareimpact", snippet="Hoe teams met Lego Serious Play draagvlak bouwen.")
    # Beide zijn even vers en delen hetzelfde keyword; het verschil moet puur uit
    # de onderwerp-boost komen. De on-topic titel scoort dus merkbaar hoger.
    assert on > off + 10


def test_blend_straft_onbewezen_relevantie():
    """match_score = -1 (gefaald/onbekend) mag geen vrijbrief zijn: het signaal
    zakt onder een even sterk signaal dat wél een relevantie-oordeel haalde."""
    from backend.domains.radar import scorer
    onbewezen = scorer.blend_scores(80.0, -1)
    bewezen = scorer.blend_scores(80.0, 85)
    assert onbewezen < 80.0                  # gedempt, geen volle score
    assert onbewezen == 65.0                 # 80 - 15 penalty
    assert bewezen > onbewezen               # bewezen-relevant wint


def test_salvage_redt_match_score_uit_kapotte_json():
    """Bijna-JSON van het gratis model verloor vroeger de relevantie (match=-1).
    De salvage vist de match_score er alsnog uit."""
    from backend.domains.radar import scorer
    raw = ('Hier is mijn antwoord:\n'
           '"hook": "Een pakkende hook over de zorg", '
           '"match_score": 72, dit is verder geen nette json')
    got = scorer._salvage_angle(raw)
    assert got is not None
    assert got["match_score"] == 72
    assert "hook" in got and got["hook"].startswith("Een pakkende")
    # Zonder enig match-veld valt er niets te redden.
    assert scorer._salvage_angle("gewoon wat losse tekst zonder velden") is None


def test_tavily_quota_logt_zichtbare_errorkaart(radar):
    """Quota op mag niet stil zijn: er hoort één status='error'-kaart per dag in
    activity_log te komen (verschijnt in het Actiecentrum), en niet gedupliceerd."""
    from backend.shared.database import get_conn

    def _count():
        with get_conn() as c:
            return len(c.execute(
                "SELECT id FROM activity_log WHERE action='radar_scan' "
                "AND status='error' AND detail LIKE 'Tavily-quota%' "
                "AND date(created_at)=date('now')").fetchall())

    assert _count() == 0
    radar._log_tavily_quota_card("WeAreImpact")
    assert _count() == 1
    radar._log_tavily_quota_card("WeAreImpact")   # tweede scan zelfde dag
    assert _count() == 1                           # dedupe: nog steeds één


def test_score_relevance_parst_en_faalt_zacht(monkeypatch):
    """De relevantie-rechter leest het cijfer uit JSON, redt het uit bijna-JSON,
    en valt op -1 (niet 0) als er echt niets te lezen is."""
    import asyncio
    from backend.domains.radar import scorer

    def _fake(text):
        async def gen(*a, **k):
            yield {"type": "text", "text": text}
        return gen

    # Nette JSON.
    monkeypatch.setattr(scorer.agent_service, "run_agent",
                        _fake('{"reden": "raakt de zorg-kern", "score": 78}'))
    assert asyncio.run(scorer.score_relevance("t", "s", "weareimpact")) == 78

    # Bijna-JSON → regex-salvage van het cijfer.
    monkeypatch.setattr(scorer.agent_service, "run_agent",
                        _fake('reden: geen fit, score: 12 (verder rommel)'))
    assert asyncio.run(scorer.score_relevance("t", "s", "weareimpact")) == 12

    # Onleesbaar → -1 (blend dempt dan, geen vrijbrief).
    monkeypatch.setattr(scorer.agent_service, "run_agent",
                        _fake('ik weet het niet zeker'))
    assert asyncio.run(scorer.score_relevance("t", "s", "weareimpact")) == -1


def test_conveyor_quality_assessment():
    from backend.domains.pipeline.conveyor import _assess_output

    # Te kort → niet ok.
    assert not _assess_output("kort", {"workspace_path": "x/listicle.md"})["ok"]
    # Goed met koppen en voldoende lengte → ok.
    good = (
        "# Titel van het artikel over vrijwilligers werven\n\n"
        "## Sectie 1: waarom dit werkt\n"
        "Een lange genoeg tekst met uitleg en context zodat de quality-check "
        "de lengte-eis van tweehonderd tekens ruimschoots haalt en we kunnen "
        "concluderen dat dit een geldig SEO-concept is met voldoende body.\n\n"
        "## Sectie 2: hoe je het toepast\n"
        "Nog meer uitleg met praktische voorbeelden en een duidelijkeStructuur "
        "die laat zien dat de agent zelfstandig bruikbare content produceert."
    )
    assert _assess_output(good, {"workspace_path": "x/listicle.md"})["ok"]
    # Listicle zonder koppen → niet ok.
    flat = ("dit is een hele lange muur van tekst zonder enige koppen of "
            "structuur die ook maar enigszins zou kunnen dienen als SEO-concept "
            "en dat is precies waarom deze test moet falen want het is waardeloos "
            "als concept en de pijplijn moet het weigeren als output.")
    assert not _assess_output(flat, {"workspace_path": "x/listicle.md"})["ok"]


def test_local_fallback_produces_concept():
    """Zonder backend levert de fallback een gemarkeerd concept (geen crash)."""
    from backend.shared.agent_runner import _local_template_fill

    async def _run():
        events = []
        async for ev in _local_template_fill(
            [{"role": "user", "content": "# Mijn testtitel\n\nBeschrijving hier"}],
            "systeem",
        ):
            events.append(ev)
        return events

    import asyncio
    events = asyncio.run(_run())
    texts = [e.get("text", "") for e in events if e.get("type") == "text"]
    joined = "\n".join(texts)
    assert "Mijn testtitel" in joined
    assert "CONCEPT" in joined
    assert "lokale fallback" in joined
