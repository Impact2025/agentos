"""De kansen-gate moet Nederlands kunnen lezen.

Aanleiding (4 aug 2026): Bewaard voor Jou bood acht 'nieuwe' kansen aan en de
gate filterde er nul — op precies het project waarvoor de gate op 3 augustus
was aangescherpt, één dag later. Twee oorzaken, allebei taalkundig:

  * **vraagwoorden telden mee als onderwerp.** 'hoe schrijf je een levensverhaal
    op' hield drie tokens over ('hoe', 'schrijf', 'levensverhaal') tegen twee
    van de live pagina '/levensverhaal-opschrijven'. De overlap zakte daardoor
    van 50% naar 33% — en 'hoe' zegt niets over het onderwerp, alleen over de
    vraagvorm waarin iemand het stelt.
  * **de f/v-wisseling brak de stamvergelijking.** 'schrijf' en 'schrijven'
    delen zes letters ('schrij'), 66% van het langste woord — nét onder de
    ratio van 70%. Die ratio ligt daar met reden ('levensboek' /
    'levensverhaal' mag géén match zijn), dus het antwoord is niet de ratio
    verlagen maar de wisseling herkennen. Het is de standaardvervoeging van elk
    Nederlands werkwoord op -ven en -zen: brief/brieven, leef/leven.

Daar bovenop matcht de gate nu scheidbare voorvoegsels (op-, vast-, mee-),
want die maken van één werkwoord tien vormen die allemaal hetzelfde doen.
"""
import pytest

from backend.domains.seo import opportunity_quality as q


# ── Woordvormen die hetzelfde woord zijn ───────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("schrijf", "opschrijven"),   # f/v-wisseling + scheidbaar voorvoegsel
    ("leggen", "vastleggen"),
    ("nemen", "meenemen"),
    ("brief", "brieven"),         # f/v zonder voorvoegsel
    ("ouders", "ouder"),          # meervoud — de fix van 3 aug, mag niet breken
    ("individueel", "individuele"),
    ("voetbal", "voetbalskills"),
])
def test_woordvormen_matchen(a, b):
    assert q._same_word(a, b), f"{a!r} en {b!r} zijn hetzelfde woord"


@pytest.mark.parametrize("a,b", [
    ("levensverhaal", "levensboek"),   # 46% — twee onderwerpen, expliciet geen match
    ("verhaal", "levensverhaal"),      # samenstelling ≠ zijn grondwoord
    ("kosten", "kost"),                # te kort om een stam te heten
    ("erfenis", "erfgoed"),
    ("zorg", "zorgen"),
])
def test_verschillende_woorden_matchen_niet(a, b):
    assert not q._same_word(a, b), (
        f"{a!r} en {b!r} zijn verschillende onderwerpen; ze samenvoegen kost een "
        f"pagina die er had moeten komen"
    )


def test_de_ratio_blijft_de_grens_bewaken():
    """De f/v-gelijkstelling mag de stamratio niet feitelijk verlagen.

    Zou ze dat wel doen, dan glipt 'levensboek'/'levensverhaal' er alsnog door
    en verdwijnt een kans die een eigen pagina verdient — de fout die de ratio
    van 70% juist moest voorkomen.
    """
    assert q._gedeeld_begin("levensverhaal", "levensboek") == 6
    assert not q._same_word("levensverhaal", "levensboek")


# ── Vraagwoorden dragen geen onderwerp ─────────────────────────────────────

@pytest.mark.parametrize("woord", ["hoe", "wat", "waarom", "wanneer", "welke", "wie"])
def test_vraagwoorden_tellen_niet_mee(woord):
    assert woord not in q.tokens(f"{woord} levensverhaal vastleggen"), (
        "een vraagwoord verwatert de overlap-breuk en promoveert zo een "
        "bestaande pagina tot 'nieuwe kans'"
    )


def test_onderwerp_blijft_over():
    assert q.tokens("hoe schrijf je een levensverhaal op") == {"schrijf", "levensverhaal"}


# ── Het geval waar het om begonnen was ─────────────────────────────────────

def test_de_kans_van_bewaard_voor_jou():
    """'hoe schrijf je een levensverhaal op' tegen '/levensverhaal-opschrijven'.

    Die pagina stond live met 62 vertoningen op positie 26,9 toen de gate de
    kans als nieuw doorliet. Eén artikel erbij had een derde pagina in hetzelfde
    cluster gezet — er stonden er al zeven op 'levensverhaal vastleggen'.
    """
    assert q.is_same_topic("hoe schrijf je een levensverhaal op",
                           "levensverhaal opschrijven")


def test_gate_blijft_lenient_waar_dat_de_bedoeling_is():
    """Strenger matchen mag de intentie-uitzondering niet opeten.

    'kosten' erbij is een andere zoekvraag — die zoeker wil een prijs, niet nóg
    een uitleg — en verdient een eigen pagina. Dat besluit stond al in de
    duplicaat-drempel; deze test bewaakt dat de nieuwe woordregels het niet
    stilzwijgend slopen.
    """
    assert not q.is_same_topic("levensverhaal laten schrijven kosten",
                               "levensverhaal laten schrijven")
