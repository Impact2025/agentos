"""Tests voor de NL agenda-opdracht (spraak/tekst → voorstel achter de gate).

`nl_command.py` vertaalt een gesproken zin naar een datum, een duur en een
titel. Dat is precies het soort code dat verkeerd gáát zonder te falen: er komt
altijd een afspraak uit, alleen op de verkeerde dag of tien uur lang. Vier van
de fouten hieronder stonden er op 11 aug 2026 in en zijn met deze zinnen
gemeten, niet uit de code afgeleid.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.domains.calendar import nl_command as nlc

TZ = ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def dinsdag(monkeypatch):
    """Bevries 'nu' op dinsdag 11 augustus 2026, 11:00 (zomertijd)."""
    monkeypatch.setattr(nlc, "_amsterdam_now",
                        lambda: datetime(2026, 8, 11, 11, 0, tzinfo=TZ))


# ── Duur ───────────────────────────────────────────────────────────────────

def test_om_tien_uur_is_een_tijdstip_geen_duur(dinsdag):
    """De meest gewone Nederlandse formulering leverde een afspraak van tien
    uur op: de duurregex las de kloktijd (`(\\d+)\\s*uur`) als duur, dus werd
    'morgen om 10 uur tandarts' een blok van 10:00 tot 20:00."""
    cmd = nlc.parse_command("morgen om 10 uur tandarts")

    assert cmd.kind == "single"
    assert (cmd.start.hour, cmd.start.minute) == (10, 0)
    assert cmd.duration_min == 30
    assert cmd.end.hour == 10 and cmd.end.minute == 30


def test_expliciete_duur_telt_wel(dinsdag):
    cmd = nlc.parse_command("morgen 14.00 sparren, duurt 2 uur")
    assert cmd.duration_min == 120
    assert cmd.end.hour == 16


@pytest.mark.parametrize("zin,minuten", [
    ("morgen 09.00 overleg 45 min", 45),
    ("morgen 09.00 kwartier bellen", 15),
    ("morgen 09.00 standup 15 minuten", 15),
])
def test_duuraanduidingen(dinsdag, zin, minuten):
    assert nlc.parse_command(zin).duration_min == minuten


# ── Datum ──────────────────────────────────────────────────────────────────

def test_genoemde_datum_in_het_verleden_is_een_vraag_geen_verschuiving(dinsdag):
    """'5 augustus 14.00' werd op 11 augustus stilzwijgend een voorstel voor de
    12e: de +7-dagenregel (bedoeld voor een kale weekdag) werd óók op een
    genoemde datum toegepast. Een opdracht stil veranderen is erger dan hem
    weigeren — de gebruiker ziet 'voorstel aangemaakt' en gelooft het."""
    cmd = nlc.parse_command("5 augustus 14.00 evaluatie")

    assert cmd.kind == "error"
    assert "al geweest" in cmd.error
    assert "augustus" in cmd.error, "de maandnaam moet Nederlands zijn, niet 'August'"


def test_kale_weekdag_schuift_wel_op(dinsdag):
    """Zonder datum betekent een weekdag vanzelfsprekend de eerstvolgende —
    dáár is de +7-regel voor."""
    cmd = nlc.parse_command("maandag 09.00 werkoverleg")
    assert cmd.start.strftime("%d-%m") == "17-08"


def test_datum_verderop_in_het_jaar(dinsdag):
    cmd = nlc.parse_command("dinsdag 18 aug 12.15 tandarts")
    assert cmd.start.strftime("%d-%m %H:%M") == "18-08 12:15"


@pytest.mark.parametrize("zin,verwacht_titel", [
    ("vanavond 20.00 tennisen met Jeroen", "Tennisen met Jeroen"),
    ("vandaag 15.00 bellen met Sanne", "Bellen met Sanne"),
    ("vannacht 23.00 telefoontje", "Telefoontje"),
])
def test_vandaag_woorden_worden_herkend(dinsdag, zin, verwacht_titel):
    """'Vanavond 20.00 tennisen met Jeroen' gaf tot 26 aug 2026 'Ik kon geen
    datum herkennen' — alleen 'morgen'/'overmorgen'/een weekdag telden als
    datum, 'vandaag' en zijn dagdeel-varianten niet, terwijl de tijd wél
    expliciet in de zin stond. De datum-woorden mogen ook niet in de titel
    lekken (net als 'morgen'/weekdagen al niet deden)."""
    cmd = nlc.parse_command(zin)
    assert cmd.kind == "single"
    assert cmd.start.strftime("%d-%m") == "11-08"
    assert cmd.title == verwacht_titel


def test_vanmiddag_in_het_verleden_is_een_vraag_geen_verschuiving(dinsdag):
    """'Vanmiddag'/'vanmorgen' zijn net zo expliciet als een genoemde datum —
    is het tijdstip vandaag al voorbij, dan is stilzwijgend een week
    opschuiven dezelfde fout als bij '5 augustus 14.00' (zie hierboven), niet
    de +7-regel die bij een kale weekdag hoort."""
    cmd = nlc.parse_command("vanmiddag 09.00 overleg")  # 'nu' is 11:00
    assert cmd.kind == "error"
    assert "al geweest" in cmd.error


@pytest.mark.parametrize("zin,verwacht_dag,verwacht_titel", [
    ("morgen 10.00 tandarts", "12-08", "Tandarts"),
    ("morgenochtend 10.00 tandarts", "12-08", "Tandarts"),
    ("morgenmiddag 14.00 bellen", "12-08", "Bellen"),
    ("morgenavond 20.00 etentje", "12-08", "Etentje"),
    ("overmorgen 09.00 kapper", "13-08", "Kapper"),
])
def test_morgen_samenstellingen_blijven_morgen(dinsdag, zin, verwacht_dag, verwacht_titel):
    """Regressietoets op de fix hierboven: het onderscheid tussen 'vanmorgen'
    (vandaag) en 'morgen'/'morgenochtend'/'morgenmiddag'/'morgenavond'
    (morgen) mag niet ten koste gaan van de samengestelde vormen — die hebben
    geen woordgrens vóór 'ochtend'/'middag'/'avond' en zouden bij een te
    strikte \\bmorgen\\b-toets alsnog 'geen datum herkend' geven, en zonder de
    strip in _zonder_tijdsinfo lekt 'Morgenmiddag'/'Morgenavond' als titel."""
    cmd = nlc.parse_command(zin)
    assert cmd.kind == "single"
    assert cmd.start.strftime("%d-%m") == verwacht_dag
    assert cmd.title == verwacht_titel


# ── Terugkerend blok ───────────────────────────────────────────────────────

def test_wekelijks_blok_landt_op_de_gevraagde_dag(dinsdag):
    """Het blok kreeg de dag van invoeren mee (`now.replace(...)`), dus stond
    'alle vrijdagen' op een dinsdag — en de conflictcontrole keek daardoor naar
    de verkeerde dag, wat een botsend blok als vrij laat lezen."""
    cmd = nlc.parse_command("blok alle vrijdagen tussen 09.00 en 10.00")

    assert cmd.kind == "recurring"
    assert cmd.recur_weekday == 4
    assert cmd.start.weekday() == 4
    assert cmd.start.strftime("%d-%m %H:%M") == "14-08 09:00"
    assert cmd.duration_min == 60


def test_blok_met_eindtijd_voor_begintijd_faalt(dinsdag):
    cmd = nlc.parse_command("blok alle vrijdagen tussen 10.00 en 09.00")
    assert cmd.kind == "error"


# ── Titel ──────────────────────────────────────────────────────────────────

def test_titel_bevat_het_onderwerp_niet_de_datum(dinsdag):
    """De titel zegt wát het is; wannéér staat al in het tijdvak. 'donderdag om
    9 uur bellen met Marieke' als agendatitel is op de dag zelf onleesbaar."""
    cmd = nlc.parse_command("donderdag om 9 uur bellen met Marieke")
    assert cmd.title == "Bellen met Marieke"


def test_titel_behoudt_hoofdletters_in_namen(dinsdag):
    cmd = nlc.parse_command("woensdag 13.30 teams overleg met Thijs Lenting 45 min")
    assert "Thijs Lenting" in cmd.title


def test_maandnaam_wordt_nooit_het_onderwerp(dinsdag):
    """'5 augustus 14.00 evaluatie' kreeg de titel 'Augustus': de fallback pakte
    het eerste woord van drie letters of meer, en dat was de maand."""
    cmd = nlc.parse_command("18 augustus 14.00 evaluatie")
    assert cmd.title.lower() != "augustus"
    assert "evaluatie" in cmd.title.lower()


# ── Randgevallen ───────────────────────────────────────────────────────────

def test_zonder_datum_geen_gok(dinsdag):
    """Liever een vraag dan een verzonnen moment in de agenda."""
    cmd = nlc.parse_command("tandarts")
    assert cmd.kind == "error"
    assert "datum" in cmd.error.lower()


def test_lege_opdracht(dinsdag):
    assert nlc.parse_command("   ").kind == "error"


def test_online_afspraak_wordt_herkend(dinsdag):
    """Relevant voor de reisbuffer: online betekent geen reistijd eromheen."""
    cmd = nlc.parse_command("morgen 10.00 teams met Marieke")
    assert cmd.is_remote is True


# ── Ambigue "X uur" (26 aug 2026) ──────────────────────────────────────────

@pytest.mark.parametrize("zin,verwacht_uur", [
    ("aanstaande vrijdag om 1 uur naar de tandarts", 13),
    ("volgende week woensdag om 3 uur heb ik een gesprek met gemeente", 15),
])
def test_ambigue_uur_wordt_middag_of_avond(dinsdag, zin, verwacht_uur):
    """'1 uur'/'3 uur' zonder dagdeel werd letterlijk 01:00/03:00 gelezen —
    twee voorstellen (tandarts, gemeente) werden zo geboekt midden in de
    nacht. Een tandarts- of gemeenteafspraak om 01:00/03:00 's nachts komt in
    de praktijk niet voor, dus schuiven uren 1–7 naar de middag/avond tenzij
    er een expliciete nachtaanduiding bij staat."""
    cmd = nlc.parse_command(zin)
    assert cmd.kind == "single"
    assert cmd.start.hour == verwacht_uur


def test_vannacht_blijft_nacht():
    """Een expliciete nachtaanduiding mag niet worden omgezet."""
    assert nlc._resolveer_ambigue_uur(3, "vannacht om 3 uur telefoontje") == 3


def test_tien_uur_blijft_ochtend(dinsdag):
    """Regressie: uren boven 7 (ochtendafspraken zijn heel normaal) mogen niet
    worden aangeraakt."""
    cmd = nlc.parse_command("morgen om 10 uur tandarts")
    assert cmd.start.hour == 10


# ── Hele dag / vrij (26 aug 2026) ───────────────────────────────────────────

def test_helemaal_vrij_zijn_is_een_hele_dag_blok(dinsdag):
    """'Ik wil volgende week woensdag helemaal vrij zijn om te zeilen' leverde
    een 30-minuten-afspraak 'Afspraak' om 10:00–10:30 op in plaats van de hele
    dag geblokkeerd — precies het tegenovergestelde van de opdracht."""
    cmd = nlc.parse_command(
        "Ik wil volgende week woensdag helemaal vrij zijn om te zeilen")
    assert cmd.kind == "single"
    assert cmd.all_day is True
    assert cmd.start.hour == 0 and cmd.end.hour == 0
    assert (cmd.end - cmd.start).days == 1


def test_vrijdag_als_weekdag_triggert_geen_hele_dag(dinsdag):
    """Regressie op een aanpalende regex-fout: 'vrij\\s*dag' (nul-of-meer
    spaties) matchte ook de aaneengeschreven weekdag 'vrijdag' zelf, dus zou
    élke afspraak die de dag noemt zonder expliciete tijd all_day worden."""
    cmd = nlc.parse_command("vrijdag bellen met Sanne")
    assert cmd.kind == "single"
    assert cmd.all_day is False
    assert cmd.start.hour == 10  # default, geen hele dag


# ── Locatie (voor de reistijd-buffer, 25 aug 2026) ─────────────────────────

def test_bij_locatie_wordt_herkend(dinsdag):
    cmd = nlc.parse_command("dinsdag 14 uur bij de tandarts")
    assert cmd.location == "Tandarts"


def test_bij_locatie_stopt_bij_volgend_voorzetsel(dinsdag):
    cmd = nlc.parse_command("donderdag 15 uur bij de notaris om het contract te tekenen")
    assert cmd.location == "Notaris"


def test_online_afspraak_krijgt_geen_locatie(dinsdag):
    cmd = nlc.parse_command("morgen 10.00 teams bij de klant")
    assert cmd.location == ""


def test_geen_bij_geeft_lege_locatie(dinsdag):
    cmd = nlc.parse_command("dinsdag 14 uur teamoverleg")
    assert cmd.location == ""
