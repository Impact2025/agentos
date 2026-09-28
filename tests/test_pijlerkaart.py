"""Pijlerkaart: per zoekterm één pagina.

Aanleiding (27 sep 2026): 16 concepten voor Bewaard voor Jou in de wachtrij,
waarvan zeven een pagina dupliceerden die al rankte. De Gauntlet- en
goal-concepten hebben een leeg `keyword`, dus de zoekwoord-dedupe in
`create_job` zag ze niet; en de Optimizer stelde 'levensboek maken' → /kennisbank
voor, weg van de pijler. Deze tests leggen de échte titels uit die wachtrij vast.
"""
import pytest

from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import optimizer
from backend.domains.seo.pillars import pillar_conflict, pillar_for_text
from backend.shared.database import get_conn

BVJ = {"base_url": "https://bewaardvoorjou.nl"}


class TestPijlerConflict:
    @pytest.mark.parametrize("titel,pad", [
        ("Je levensverhaal vastleggen: zo pak je het aan", "/levensverhaal-vastleggen"),
        ("Levensverhaal opschrijven zonder ervaring? Zo start je", "/levensverhaal-opschrijven"),
        ("Zo schrijf je een levensverhaal op: van eerste gesprek tot gedrukt boek",
         "/levensverhaal-opschrijven"),
        ("Wat kost het vastleggen van een levensverhaal?",
         "/kennisbank/levensverhaal-laten-schrijven-kosten"),
        ("Levensverhaal vastleggen: wat het kost, hoe lang het duurt",
         "/kennisbank/levensverhaal-laten-schrijven-kosten"),
        ("Cadeau voor ouders die alles al hebben: 7 ideeën die blijven",
         "/blog/7-persoonlijke-cadeaus-voor-ouders-die-alles-al-hebben"),
        ("Levensboek maken: zo leg je iemands levensverhaal vast",
         "/kennisbank/van-digitaal-verhaal-naar-tastbaar-levensboek-exporteren"),
    ])
    def test_dubbele_concepten_uit_de_wachtrij_worden_herkend(self, titel, pad):
        assert pillar_for_text(BVJ, titel)[1] == pad
        assert pillar_conflict(BVJ, titel) is not None

    @pytest.mark.parametrize("titel", [
        "Erfstukken digitaliseren en bewaren: zo leg je het verhaal vast",
        "Interviewvragen voor oma over vroeger: 25 warme vragen",
        "Mijlpaalcadeau voor 50 jaar getrouwd",
        "Geboortecadeau om later te bewaren: 7 cadeaus",
    ])
    def test_eigen_onderwerpen_mogen_door(self, titel):
        assert pillar_conflict(BVJ, titel) is None

    def test_verbetering_op_de_slug_van_de_pijler_mag(self):
        titel = "Kraamcadeau voor ouders die al alles hebben: 7 cadeaus"
        assert pillar_conflict(BVJ, titel, slug="kraamcadeau-ouders-die-al-alles-hebben") is None

    def test_verbetering_onder_een_nieuwe_slug_is_een_tweede_pagina(self):
        titel = "Kraamcadeau voor ouders die al alles hebben: 7 cadeaus"
        assert pillar_conflict(
            BVJ, titel, slug="kraamcadeau-voor-ouders-die-al-alles-hebben-7-cadeaus-die"
        ) is not None

    def test_site_zonder_pijlerkaart_blokkeert_nooit(self):
        assert pillar_conflict({"base_url": "https://voorbeeld.nl"},
                               "Je levensverhaal vastleggen") is None

    def test_www_en_gsc_property_vinden_dezelfde_kaart(self):
        titel = "Je levensverhaal vastleggen"
        assert pillar_conflict({"base_url": "https://www.bewaardvoorjou.nl"}, titel)
        assert pillar_conflict({"gsc_property": "sc-domain:bewaardvoorjou.nl"}, titel)


@pytest.fixture
def bvj_job():
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sites (id, name, base_url, created_at) VALUES (?,?,?,?)",
            ("site-pijler", "Bewaard voor Jou", "https://bewaardvoorjou.nl",
             "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO content_jobs (id, site_id, title, keyword, rationale, status, "
            "blog_html, seo_score, slug, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("job-pijler", "site-pijler", "Je levensverhaal vastleggen: zo pak je het aan",
             "", "Gauntlet", "pending_review", "<p>Tekst.</p>", 95.0,
             "je-levensverhaal-vastleggen-zo-pak-je-het-aan", "2026-09-18T15:05:30"),
        )
    yield "job-pijler"
    with get_conn() as conn:
        conn.execute("DELETE FROM content_jobs WHERE id='job-pijler'")
        conn.execute("DELETE FROM sites WHERE id='site-pijler'")


async def test_goedkeuren_weigert_een_concept_op_een_pijlerterm(bvj_job):
    with pytest.raises(ValueError, match="/levensverhaal-vastleggen"):
        await cp.approve_and_publish(bvj_job)
    assert cp.get_job(bvj_job)["status"] == "pending_review"


def test_linksuggestie_met_pijleranker_wijst_alleen_naar_de_pijler():
    def pagina(tekst):
        return {"text": tekst, "text_lower": tekst.lower(), "links": set()}

    bron = "https://bewaardvoorjou.nl/blog/narratieve-zorg"
    pages = {
        bron: pagina("Wie een levensboek maken wil, begint bij de vragen."),
        "https://bewaardvoorjou.nl/kennisbank": pagina("Overzicht"),
        "https://bewaardvoorjou.nl/kennisbank/van-digitaal-verhaal-naar-tastbaar-levensboek-exporteren":
            pagina("Exporteren"),
    }
    top = {url: {"query": "levensboek maken"} for url in pages if url != bron}
    links = optimizer._analyze_internal_links(pages, top, [], BVJ)
    doelen = {s["data"]["to"] for s in links}
    assert "https://bewaardvoorjou.nl/kennisbank" not in doelen
    assert ("https://bewaardvoorjou.nl/kennisbank/"
            "van-digitaal-verhaal-naar-tastbaar-levensboek-exporteren") in doelen
