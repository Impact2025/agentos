"""Outreach-leerlus: variant-spreiding, meting en les-destillatie."""
import json
from datetime import datetime, timedelta, timezone


def _seed_lead(conn, lead_id, variant, *, replied, days_ago=20):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO leads (id, org_name, status, contacted_at, replied_at, "
        "outreach_variant, created_at, updated_at) VALUES (?, ?, 'contacted', ?, ?, ?, ?, ?)",
        (lead_id, f"Org {lead_id}", ts, ts if replied else "",
         json.dumps(variant), ts, ts),
    )


def _seed_dimension(conn, dim, winner, loser, *, winner_replies, loser_replies, n=10):
    """n leads per waarde; winner_replies/loser_replies daarvan reageerden."""
    from backend.domains.prospecting.learning import VARIANT_DIMENSIONS
    other_dims = {d: v[0] for d, v in VARIANT_DIMENSIONS.items() if d != dim}
    for i in range(n):
        _seed_lead(conn, f"{dim}-w-{i}", {dim: winner, **other_dims},
                   replied=i < winner_replies)
        _seed_lead(conn, f"{dim}-l-{i}", {dim: loser, **other_dims},
                   replied=i < loser_replies)
    conn.commit()


# ── Variant-keuze ──────────────────────────────────────────────────────────

def test_choose_variant_deterministisch_en_geldig():
    from backend.domains.prospecting.learning import choose_variant, VARIANT_DIMENSIONS

    v1 = choose_variant("lead-123")
    v2 = choose_variant("lead-123")
    assert v1 == v2
    assert set(v1) == set(VARIANT_DIMENSIONS)
    for dim, value in v1.items():
        assert value in VARIANT_DIMENSIONS[dim]


def test_choose_variant_spreidt_over_leads():
    from backend.domains.prospecting.learning import choose_variant

    openings = {choose_variant(f"lead-{i}")["opening"] for i in range(50)}
    assert openings == {"observatie", "vraag"}


def test_variant_instructions_dekt_alle_dimensies():
    from backend.domains.prospecting.learning import (
        choose_variant, variant_instructions, VARIANT_DIMENSIONS,
    )

    lines = variant_instructions(choose_variant("x"))
    assert len(lines) == len(VARIANT_DIMENSIONS)


# ── Promptintegratie ───────────────────────────────────────────────────────

def test_draft_prompt_bevat_variant_en_geleerde_les(clean_tables):
    from backend.shared import learning
    from backend.domains.prospecting.outreach import _draft_prompt

    learning.upsert_lesson("outreach", "Outreach met opening 'observatie' levert "
                                       "meer replies op dan 'vraag'.")
    lead = {"org_name": "Zorggroep Test", "summary": "Thuiszorg in Utrecht"}
    variant = {"opening": "vraag", "toon": "warm", "lengte": "kort"}
    prompt = _draft_prompt(lead, variant)
    assert "specifieke, oprechte vraag" in prompt
    assert "maximaal 90 woorden" in prompt.lower()
    assert "Geleerde lessen" in prompt
    # De vaste basis-eis is vervangen door de variant-eis
    assert "Maximaal 130 woorden" not in prompt


def test_draft_prompt_zonder_variant_behoudt_basis(clean_tables):
    from backend.domains.prospecting.outreach import _draft_prompt

    prompt = _draft_prompt({"org_name": "X", "summary": "y"})
    assert "Maximaal 130 woorden" in prompt


# ── Meting en evaluatie ────────────────────────────────────────────────────

def test_eval_maakt_les_en_voorspelling_bij_duidelijke_kloof(conn, clean_tables):
    from backend.shared import learning
    from backend.domains.prospecting.learning import run_outreach_learning_eval

    # opening: observatie 5/10 replies vs vraag 0/10 → kloof 50 pp
    _seed_dimension(conn, "opening", "observatie", "vraag",
                    winner_replies=5, loser_replies=0)
    out = run_outreach_learning_eval()
    assert any("observatie" in l for l in out["lessons"])
    lessons = learning.active_lessons("outreach")
    assert any("opening 'observatie'" in l["lesson"] for l in lessons)
    preds = learning.predictions("outreach", status="open")
    assert any(p["context"] == "opening:observatie>vraag" for p in preds)


def test_eval_leert_niets_onder_steekproefdrempel(conn, clean_tables):
    from backend.shared import learning
    from backend.domains.prospecting.learning import run_outreach_learning_eval

    _seed_dimension(conn, "opening", "observatie", "vraag",
                    winner_replies=3, loser_replies=0, n=4)  # 4 < MIN_PER_VALUE
    out = run_outreach_learning_eval()
    assert out["lessons"] == []
    assert learning.active_lessons("outreach") == []


def test_eval_leert_niets_bij_kleine_kloof(conn, clean_tables):
    from backend.domains.prospecting.learning import run_outreach_learning_eval

    # 3/10 vs 3/10 → kloof 0 pp
    _seed_dimension(conn, "toon", "direct", "warm",
                    winner_replies=3, loser_replies=3)
    assert run_outreach_learning_eval()["lessons"] == []


def test_verse_mails_rijpen_eerst(conn, clean_tables):
    from backend.domains.prospecting.learning import variant_stats

    variant = {"opening": "observatie", "toon": "direct", "lengte": "kort"}
    _seed_lead(conn, "vers", variant, replied=True, days_ago=1)   # nog niet rijp
    _seed_lead(conn, "rijp", variant, replied=True, days_ago=20)
    conn.commit()
    assert variant_stats()["opening"]["observatie"]["sent"] == 1


def test_resolver_geeft_actuele_kloof(conn, clean_tables):
    from backend.domains.prospecting.learning import _resolver

    _seed_dimension(conn, "lengte", "kort", "middel",
                    winner_replies=4, loser_replies=1)
    gap = _resolver("reply_rate_gap", "lengte:kort>middel")
    assert gap == 30.0  # 40% - 10%
    assert _resolver("reply_rate_gap", "onzin") is None
    assert _resolver("iets_anders", "lengte:kort>middel") is None


def test_herhaalde_eval_dedupet_les_en_voorspelling(conn, clean_tables):
    from backend.shared import learning
    from backend.domains.prospecting.learning import run_outreach_learning_eval

    _seed_dimension(conn, "opening", "observatie", "vraag",
                    winner_replies=5, loser_replies=0)
    run_outreach_learning_eval()
    run_outreach_learning_eval()
    lessons = [l for l in learning.active_lessons("outreach")
               if "opening" in l["lesson"]]
    assert len(lessons) == 1
    assert lessons[0]["times_confirmed"] == 2
    preds = [p for p in learning.predictions("outreach", status="open")
             if p["context"].startswith("opening:")]
    assert len(preds) == 1
