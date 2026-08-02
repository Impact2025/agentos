"""Dashboard-tips moeten waar én uitvoerbaar zijn.

Aanleiding (25 jul 2026, Steentjebijsteentje): het dashboard toonde bovenaan
"CTR 0.0% is laag — verbeter meta descriptions en titels" bij een gemiddelde
positie van 45.6. Op positie 45 ís 0% CTR de verwachte waarde; er viel niets te
verbeteren aan de snippet. Tegelijk werd 'origineel jubileum cadeau' als nieuw
artikel voorgesteld terwijl er al een job over hetzelfde onderwerp vaststond op
`needs_work`. Twee tips, allebei geen werk maar ruis.
"""
import re
import uuid
from datetime import datetime, timezone

import pytest

from backend.domains.projects.router import (
    _keyword_already_covered,
    _pipeline_keywords,
)
from backend.domains.publish.content_pipeline import slugify_title
from backend.domains.seo.optimizer import _expected_ctr


# ── CTR-drempel: benchmark per positie i.p.v. een vaste 3% ──────────────

def _ctr_alert_fires(ctr: float, position: float, impressions: int) -> bool:
    """Repliceert de voorwaarde uit `project_advice` (zie router.py)."""
    return (impressions > 100 and position <= 20
            and ctr < _expected_ctr(position) * 0.7)


def test_geen_ctr_alert_ver_buiten_klikbereik():
    """De precieze situatie van 25 jul: 413 impressies, 0 klikken, pos 45.6."""
    assert not _ctr_alert_fires(0.0, 45.6, 413)


def test_wel_ctr_alert_binnen_klikbereik():
    """Positie 4 met 1% CTR is wél een snippet-probleem (benchmark 7.5%)."""
    assert _ctr_alert_fires(1.0, 4.0, 413)


def test_geen_ctr_alert_bij_normale_ctr_voor_die_positie():
    """2.0% op positie 10 (benchmark 2.5%) is geen alarm waard."""
    assert not _ctr_alert_fires(2.0, 10.0, 413)


def test_geen_ctr_alert_bij_te_weinig_impressies():
    assert not _ctr_alert_fires(0.0, 5.0, 40)


# ── Dedupe: de Wachtrij telt mee als "hier wordt al aan gewerkt" ────────

def _make_site() -> str:
    from backend.shared.database import get_conn
    site_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sites (id, name, base_url, created_at) VALUES (?, ?, ?, ?)",
            (site_id, "Testsite", "https://testsite.nl",
             datetime.now(timezone.utc).isoformat()),
        )
    return site_id


def _make_job(site_id, status, keyword="", title=""):
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO content_jobs (id, site_id, title, keyword, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), site_id, title, keyword, status,
             datetime.now(timezone.utc).isoformat()),
        )


def test_job_in_wachtrij_dekt_zoekwoord(clean_tables):
    site_id = _make_site()
    _make_job(site_id, "pending_review", keyword="jubileum cadeau ideeen")
    assert "jubileum cadeau ideeen" in _pipeline_keywords(site_id)


def test_job_onder_de_kwaliteitsgate_telt_ook_mee(clean_tables):
    """`needs_work` is werk in uitvoering, geen vrij zoekwoord — dit is precies
    de job die op 25 jul opnieuw werd voorgesteld."""
    site_id = _make_site()
    _make_job(site_id, "needs_work", keyword="jubileum cadeau ideeen")
    assert _keyword_already_covered("jubileum cadeau ideeen",
                                    _pipeline_keywords(site_id))


def test_afgewezen_job_geeft_zoekwoord_weer_vrij(clean_tables):
    site_id = _make_site()
    _make_job(site_id, "rejected", keyword="jubileum cadeau ideeen")
    assert _pipeline_keywords(site_id) == set()


def test_titel_dekt_zoekwoord_bij_leeg_keyword_veld(clean_tables):
    """Goal-gestagede jobs hebben een leeg keyword-veld; het onderwerp zit dan
    alleen in de titel."""
    site_id = _make_site()
    _make_job(site_id, "pending_review", keyword="",
              title="Jubileum cadeau ideeen die echt verbinden")
    assert _keyword_already_covered("jubileum cadeau ideeen",
                                    _pipeline_keywords(site_id))


def test_variant_van_hetzelfde_onderwerp_wordt_herkend(clean_tables):
    site_id = _make_site()
    _make_job(site_id, "needs_work", keyword="",
              title="Jubileum cadeau ideeen die echt verbinden: 5 blijvende keuzes")
    assert _keyword_already_covered("origineel jubileum cadeau",
                                    _pipeline_keywords(site_id))


def test_ander_onderwerp_blijft_voorgesteld(clean_tables):
    site_id = _make_site()
    _make_job(site_id, "published", keyword="jubileum cadeau ideeen")
    assert not _keyword_already_covered("lego serious play relatiecoaching",
                                        _pipeline_keywords(site_id))


def test_enkel_kernwoord_matcht_niet_op_deelreeks(clean_tables):
    """Eén kernwoord is te weinig bewijs — anders dekt 'cadeau' alles."""
    site_id = _make_site()
    _make_job(site_id, "published", keyword="jubileum cadeau ideeen")
    assert not _keyword_already_covered("cadeau", _pipeline_keywords(site_id))


# ── Slug: afkappen op woordgrens, niet midden in een woord ─────────────

def test_lange_titel_kapt_af_op_woordgrens():
    """Een harde [:60] leverde '…-bouw-wat-woor' op: een URL die nergens naar
    verwijst en daardoor uit de sitemap viel (24 jul 2026)."""
    slug = slugify_title(
        "Non-verbale communicatie oefening voor koppels: bouw wat woorden "
        "niet kunnen zeggen")
    assert len(slug) <= 60
    assert not slug.endswith("-")
    assert "woor" not in slug.split("-")  # geen half woord aan het eind
    assert slug.split("-")[-1] in {"bouw", "wat", "woorden", "koppels"}


def test_korte_titel_blijft_intact():
    assert slugify_title("Vier microgewoontes") == "vier-microgewoontes"


def test_slug_zonder_streepjes_wordt_niet_leeg():
    slug = slugify_title("a" * 80)
    assert slug and len(slug) <= 60


# ── Slug: witte lijst, geen zwarte lijst ────────────────────────────────
#
# De oude slugify verving een handjevol leestekens en liet de rest staan. Zo
# gingen 'levensverhaal-vastleggen-complete-gids-+-casestudy-anton-(12' en
# 'schrijf-meta-titel-&-description-voor-pagina-2' live — allebei een harde 404,
# want een '&' of '(' in een pad overleeft geen enkele route-matching.

@pytest.mark.parametrize("titel", [
    "Levensverhaal vastleggen: complete gids + casestudy Anton (12 jaar)",
    "Schrijf meta-titel & -description voor Pagina 2",
    "Bedrijfsuitje Hoofddorp Schiphol – Jouw teambeleving in de lucht",
    "Feedback verwerken in je contentkalender | 3 praktische stappen",
    "Plan: Directe antwoorden toevoegen aan alle 28 pagina's — Bijeen",
    'Wat kost een "levensverhaal"? 100% eerlijk antwoord!',
])
def test_slug_bevat_alleen_url_veilige_tekens(titel):
    slug = slugify_title(titel)
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug), slug


def test_accenten_worden_ontleed_niet_verwijderd():
    """'ideeën' hoort 'ideeen' te worden, niet 'ideen' — anders matcht de slug
    het zoekwoord niet meer."""
    assert "ideeen" in slugify_title("7 ideeën die binden")
    assert slugify_title("Café") == "cafe"
    assert slugify_title("Señor Ángel") == "senor-angel"


def test_titel_zonder_bruikbare_tekens_geeft_lege_slug():
    """Beter een lege slug dan een slug van losse koppeltekens: de aanroeper
    kan op leeg controleren, op '---' niet."""
    assert slugify_title("!!! ??? ...") == ""
    assert slugify_title("") == ""
