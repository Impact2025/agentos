"""De Bijeen-lanceringscampagne: van plan-bestand naar kaart in het Actiecentrum.

Wat hier bewaakt wordt is niet "de import werkt" maar de drie plekken waar een
uitgeschreven plan stil kan sneuvelen:

  1. Het videoplan raakt los van het tekstplan. Zes van de achttien posts dragen
     een Veo-concept; verdwijnt dat bij het importeren, dan staat de tekst in
     Agent OS en het beeldplan weer in een bestand — de splitsing die
     social_campaign.py juist opheft.
  2. De kanaalregels verdwijnen. "Zet de link in de eerste reactie" is precies
     het soort afspraak dat in een document blijft liggen en het verschil maakt
     tussen bereik en geen bereik.
  3. Er is geen kaart op de dag zelf. De invariant `campagnepost_over_datum`
     meldt pas na een dag speling plus de 'stil'-drempel; dat is het vangnet
     voor een stilgevallen campagne, niet de werkkaart voor vanochtend.

En één regel die zwaarder weegt dan de rest: **geplaatst is iets anders dan
goedgekeurd**. Alleen het eerste telt als uitvoering.
"""
from datetime import date, timedelta

import pytest

from backend.shared import social_campaign as campagne
from backend.shared import social_content as sc
from backend.shared.database import get_conn
from backend.domains.action_center import service as action_center
from backend.domains.iris import integrity

PROJECT = "bijeen"
START = date(2026, 8, 17)          # een maandag


@pytest.fixture()
def campagne_geimporteerd():
    res = campagne.importeer_campagne(PROJECT, start=START)
    assert res["success"], res
    return res


def _pack(post_id: str):
    agenda = campagne.agenda(PROJECT)
    rij = next(a for a in agenda if a["post"] == post_id)
    return sc.get_pack(rij["pack_id"])


def test_alle_achttien_posts_met_het_juiste_ritme(campagne_geimporteerd):
    assert campagne_geimporteerd["nieuw"] == 18
    agenda = campagne.agenda(PROJECT)
    assert len(agenda) == 18
    # Ma/wo/vr, zes weken: de eerste op de startmaandag, de laatste zes weken
    # later op vrijdag. Een campagne die halverwege de week begint verliest zijn
    # ritme, en het ritme is de helft van wat dit plan laat werken.
    assert agenda[0]["gepland"][:10] == "2026-08-17"
    assert agenda[-1]["gepland"][:10] == "2026-09-25"
    dagen = {campagne.datetime.fromisoformat(a["gepland"]).weekday() for a in agenda}
    assert dagen == {0, 2, 4}


def test_import_is_idempotent(campagne_geimporteerd):
    opnieuw = campagne.importeer_campagne(PROJECT, start=START)
    assert opnieuw["nieuw"] == 0
    assert opnieuw["bijgewerkt"] == 18
    assert len(campagne.agenda(PROJECT)) == 18


def test_veo_prompts_overleven_de_import(campagne_geimporteerd):
    """Precies zes posts dragen bewegend beeld — niet elke post verdient een film."""
    met_video = [a for a in campagne.agenda(PROJECT)
                 if (sc.get_pack(a["pack_id"]).tiktok_pack or {}).get("script")]
    assert len(met_video) == 6
    lancering = _pack("1.1").tiktok_pack
    assert "Cinematic slow push-in" in lancering["script"]
    assert "--ar 16:9" in lancering["script"]
    assert lancering["voiceover_cues"]          # de edit-instructie
    # De late avond heeft een tweede shot voor de omslag; die mag niet wegvallen.
    assert len(_pack("2.1").tiktok_pack["shotlist"]) == 2


def test_kanaalregel_staat_naast_de_tekst(campagne_geimporteerd):
    """De regel die anders in het document blijft liggen."""
    wmo = _pack("1.3")
    assert "eerste reactie" in wmo.angle.lower()
    assert "Plaatstijden" in wmo.angle
    # Post 6.3 draagt een oningevulde plaatshouder; die moet zichtbaar blijven
    # zodat hij niet per ongeluk zo de deur uit gaat.
    assert "[VUL IN" in _pack("6.3").copy["linkedin"]


def test_teksten_gaan_woordelijk_mee(campagne_geimporteerd):
    """Geen model over de copy: wat je goedkeurt is wat je plaatst."""
    li = _pack("1.1").copy["linkedin"]
    assert li.startswith("Vandaag zetten we Bijeen live.")
    assert "Geen creditcard nodig. Eerste event gratis. Klaar in 10 minuten." in li
    # De hashtag-vangnetten mogen niets verdubbelen: het plan schrijft ze zelf uit.
    assert li.count("#welzijn") == 1
    assert li.count("#Bijeen") == 1


def test_geen_kaart_voor_werk_van_volgende_week(campagne_geimporteerd):
    """Een inbox die volloopt met posts van over drie weken is geen inbox meer."""
    kaarten = [i for i in action_center.build_inbox()["items"]
               if i["kind"] == "campagne_post"]
    assert kaarten == []


def _zet_op_vandaag(post_id: str) -> str:
    rij = next(a for a in campagne.agenda(PROJECT) if a["post"] == post_id)
    with get_conn() as conn:
        conn.execute("UPDATE social_posts SET scheduled_for=? WHERE id=?",
                     (date.today().isoformat() + "T08:45:00", rij["pack_id"]))
    return rij["pack_id"]


def test_kaart_verschijnt_op_de_dag_zelf(campagne_geimporteerd):
    pack_id = _zet_op_vandaag("1.1")
    kaarten = [i for i in action_center.build_inbox()["items"]
               if i["kind"] == "campagne_post"]
    assert len(kaarten) == 1
    kaart = kaarten[0]
    assert kaart["id"] == pack_id
    assert "1.1" in kaart["title"]
    knoppen = {a["type"] for a in kaart["actions"]}
    assert {"campagne_posted", "campagne_skip"} <= knoppen


def test_geplaatst_is_niet_hetzelfde_als_goedgekeurd(campagne_geimporteerd):
    """Goedgekeurd betekent 'mag naar buiten', geplaatst betekent 'is naar buiten'.

    Alleen het tweede telt als uitvoering. Zou goedkeuren de post afmelden, dan
    is een campagne waarin nul posts de deur uitgingen niet te onderscheiden van
    een die volledig gedraaid heeft.
    """
    pack_id = _zet_op_vandaag("1.1")
    sc.approve_pack(pack_id)
    assert sc.get_pack(pack_id).status == "approved"
    assert sc.get_pack(pack_id).posted_result.get("_platforms") is None

    res = sc.mark_posted_manually(pack_id, ["linkedin", "facebook"])
    assert res["success"]
    pack = sc.get_pack(pack_id)
    assert pack.status == "posted"
    # Alleen wat écht geplaatst is telt mee — niet alle vier de kanalen.
    assert pack.posted_result["_platforms"] == ["facebook", "linkedin"]
    assert pack.posted_result["linkedin"]["via"] == "handmatig"


def test_bevestigde_post_verdwijnt_uit_inbox_en_uit_de_audit(campagne_geimporteerd):
    """Een alarm dat aantoonbaar liegt leert een mens alle alarmen te negeren.

    Bewust post 3.1 en niet 1.1: de tests binnen deze module delen één database,
    en een pack dat een eerdere test al op 'posted' zette wordt door een volgende
    import overgeslagen (dat is precies de bedoelde bescherming — een herimport
    mag geen menselijk besluit terugdraaien).
    """
    pack_id = _zet_op_vandaag("3.1")
    # Zet hem ruim over datum, zodat de invariant hem zou moeten zien.
    verstreken = (date.today() - timedelta(days=4)).isoformat() + "T08:45:00"
    with get_conn() as conn:
        conn.execute("UPDATE social_posts SET scheduled_for=? WHERE id=?", (verstreken, pack_id))

    toets = next(i for i in integrity.INVARIANTEN if i.key == "campagnepost_over_datum")
    assert [b for b in toets.check() if b.subject.endswith(":3.1")]

    sc.mark_posted_manually(pack_id, ["linkedin"])
    assert not [b for b in toets.check() if b.subject.endswith(":3.1")]
    assert not [i for i in action_center.build_inbox()["items"]
                if i["kind"] == "campagne_post" and i["id"] == pack_id]


def test_overslaan_haalt_de_post_uit_beeld(campagne_geimporteerd):
    pack_id = _zet_op_vandaag("1.2")
    sc.reject_pack(pack_id)
    assert sc.get_pack(pack_id).status == "rejected"
    assert not [i for i in action_center.build_inbox()["items"]
                if i["kind"] == "campagne_post" and i["id"] == pack_id]
