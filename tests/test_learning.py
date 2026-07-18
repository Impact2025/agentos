"""Generiek leer-raamwerk: lessen, voorspellingen, bewijs-gewogen vertrouwen."""


# ── Lessen ─────────────────────────────────────────────────────────────────

def test_upsert_dedupet_en_telt_bevestigingen(clean_tables):
    from backend.shared import learning

    a = learning.upsert_lesson("test", "Korte mails werken beter.")
    b = learning.upsert_lesson("test", "Korte  mails, werken beter?")  # zelfde, andere opmaak
    assert a == b
    lessons = learning.active_lessons("test")
    assert len(lessons) == 1
    assert lessons[0]["times_confirmed"] == 2


def test_zelfde_les_ander_agent_is_aparte_les(clean_tables):
    from backend.shared import learning

    a = learning.upsert_lesson("agent-a", "Korte mails werken beter.")
    b = learning.upsert_lesson("agent-b", "Korte mails werken beter.")
    assert a != b


def test_lege_les_geeft_none(clean_tables):
    from backend.shared import learning

    assert learning.upsert_lesson("test", "   ") is None
    assert learning.upsert_lesson("", "iets") is None


def test_ingetrokken_les_wordt_niet_heropgevoerd(conn, clean_tables):
    from backend.shared import learning

    lid = learning.upsert_lesson("test", "Deze les blijkt fout.")
    conn.execute("UPDATE agent_lessons SET active = 0 WHERE id = ?", (lid,))
    conn.commit()
    assert learning.upsert_lesson("test", "Deze les blijkt fout.") is None
    assert learning.active_lessons("test") == []


def test_lessons_block_leeg_zonder_lessen_en_gevuld_met(clean_tables):
    from backend.shared import learning

    assert learning.lessons_block("test") == ""
    learning.upsert_lesson("test", "Openen met een observatie werkt.")
    learning.upsert_lesson("test", "Openen met een observatie werkt.")
    block = learning.lessons_block("test")
    assert "Openen met een observatie werkt." in block
    assert "2× bevestigd" in block


# ── Voorspellingen ─────────────────────────────────────────────────────────

def test_prediction_validatie_en_dedupe(clean_tables):
    from backend.shared import learning

    ok = learning.record_prediction("test", metric="m", direction="up",
                                    baseline=10.0, statement="stijgt")
    assert ok is not None
    # Dedupe op (agent, metric, context, direction)
    assert learning.record_prediction("test", metric="m", direction="up",
                                      baseline=11.0, statement="nog eens") is None
    # Ongeldige input
    assert learning.record_prediction("test", metric="m", direction="zijwaarts",
                                      baseline=1.0, statement="x") is None
    assert learning.record_prediction("test", metric="m2", direction="up",
                                      baseline=None, statement="x") is None
    # threshold zonder target is niet toetsbaar
    assert learning.record_prediction("test", metric="m3", direction="up",
                                      baseline=1.0, statement="x",
                                      comparison="threshold") is None


def _eval_one(learning, agent, resolver):
    return learning.evaluate_due(agent, resolver, today="2999-01-01")


def test_trend_correct_wrong_en_ruis(clean_tables):
    from backend.shared import learning

    learning.record_prediction("t1", metric="clicks", direction="up",
                               baseline=10.0, statement="stijgt", noise=1.0)
    v = _eval_one(learning, "t1", lambda m, c: 15.0)
    assert v["correct"] == 1

    learning.record_prediction("t2", metric="clicks", direction="up",
                               baseline=10.0, statement="stijgt", noise=1.0)
    v = _eval_one(learning, "t2", lambda m, c: 5.0)
    assert v["wrong"] == 1

    learning.record_prediction("t3", metric="clicks", direction="up",
                               baseline=10.0, statement="stijgt", noise=1.0)
    v = _eval_one(learning, "t3", lambda m, c: 10.4)  # binnen de ruis
    assert v["unclear"] == 1


def test_trend_lower_is_better_positie_semantiek(clean_tables):
    from backend.shared import learning

    # GSC-positie: 'up' (beter) = getal daalt.
    learning.record_prediction("pos", metric="position", direction="up",
                               baseline=20.0, statement="klimt",
                               lower_is_better=True, noise=0.5)
    v = _eval_one(learning, "pos", lambda m, c: 12.0)
    assert v["correct"] == 1


def test_threshold_haalt_drempel_wel_en_niet(clean_tables):
    from backend.shared import learning

    learning.record_prediction("th", metric="gap", direction="up", baseline=6.0,
                               statement="kloof blijft", comparison="threshold",
                               target=1.0)
    v = _eval_one(learning, "th", lambda m, c: 2.5)
    assert v["correct"] == 1

    learning.record_prediction("th2", metric="gap", direction="up", baseline=6.0,
                               statement="kloof blijft", comparison="threshold",
                               target=1.0)
    v = _eval_one(learning, "th2", lambda m, c: -0.5)
    assert v["wrong"] == 1


def test_onmeetbaar_wordt_unclear(clean_tables):
    from backend.shared import learning

    learning.record_prediction("nm", metric="x", direction="up",
                               baseline=1.0, statement="?")
    v = _eval_one(learning, "nm", lambda m, c: None)
    assert v["unclear"] == 1 and v["correct"] == 0 and v["wrong"] == 0


def test_kapotte_resolver_breekt_evaluatie_niet(clean_tables):
    from backend.shared import learning

    learning.record_prediction("boom", metric="x", direction="up",
                               baseline=1.0, statement="?")

    def resolver(m, c):
        raise RuntimeError("boem")

    v = _eval_one(learning, "boom", resolver)
    assert v["unclear"] == 1


# ── Bewijs-gewogen vertrouwen ──────────────────────────────────────────────

def test_confidence_stijgt_en_les_wordt_ingetrokken_bij_falen(clean_tables):
    from backend.shared import learning

    lid = learning.upsert_lesson("ev", "Aanpak X werkt.")
    # Eén correcte voorspelling → vertrouwen boven 0.5
    learning.record_prediction("ev", metric="m", context="a", direction="up",
                               baseline=1.0, statement="x", lesson_id=lid, noise=0.1)
    _eval_one(learning, "ev", lambda m, c: 5.0)
    lesson = learning.active_lessons("ev")[0]
    assert lesson["confidence"] > 0.5

    # Drie foute voorspellingen erbij → intrekking (≥3 metingen, < 34% raak)
    for ctx in ("b", "c", "d"):
        learning.record_prediction("ev", metric="m", context=ctx, direction="up",
                                   baseline=10.0, statement="x", lesson_id=lid, noise=0.1)
        _eval_one(learning, "ev", lambda m, c: 1.0)
    assert learning.active_lessons("ev") == []


def test_track_record_telt_uitslagen(clean_tables):
    from backend.shared import learning

    learning.record_prediction("tr", metric="m", context="a", direction="up",
                               baseline=1.0, statement="x", noise=0.1)
    learning.record_prediction("tr", metric="m", context="b", direction="up",
                               baseline=10.0, statement="x", noise=0.1)
    _eval_one(learning, "tr", lambda m, c: 5.0)
    rec = learning.track_record("tr")
    assert rec["correct"] == 1 and rec["wrong"] == 1
    assert rec["accuracy"] == 50.0
