"""Op een verzending antwoord je niet, en je plant er geen afspraak uit in.

Aanleiding (1 aug 2026): na vijf dagen offline stonden er vijf concept-
antwoorden klaar op nieuwsbrieven (Eurostar, GetYourGuide, HomeExchange,
SkyShowtime, Offertevergelijker) en één agenda-voorstel voor 30 mei 2027,
geraapt uit een nieuwsbriefartikel over Apple en AI.

Deze tests leggen de drie oorzaken vast zodat ze niet terugkomen:
  1. de afmeld-frase-lijst was Engelstalig, terwijl de mail Nederlands is;
  2. hints werden als sub-string over de héle body geteld ("wat" in
     "watersport", "hebben we" in een essay van 7000 tekens);
  3. de Graph-flow vroeg de headers nooit op, dus daar was bulk-bewijs
     principieel onzichtbaar.
"""
import email

from backend.domains.mail import bulk, classify
from backend.domains.mail.inbox import _should_ignore


# ── 1. Headers zijn het harde bewijs ────────────────────────────────────────

def test_list_unsubscribe_header_maakt_het_bulk():
    msg = email.message_from_string(
        "From: hallo@email.skyshowtime.com\n"
        "Subject: Augustus gaat je verrassen\n"
        "List-Unsubscribe: <https://skyshowtime.com/unsub?x=1>\n"
        "\n"
        "Waar heb jij zin in? Bekijk het aanbod.\n"
    )
    reden = bulk.bulk_reason(msg, "hallo@email.skyshowtime.com", "Augustus", "Waar heb jij zin in?")
    assert reden and "list-unsubscribe" in reden.lower()


def test_precedence_bulk_maakt_het_bulk():
    msg = email.message_from_string(
        "From: nieuws@merk.nl\nSubject: Aanbieding\nPrecedence: bulk\n\nHallo!\n")
    assert bulk.is_bulk(msg, "nieuws@merk.nl", "Aanbieding", "Hallo!")


def test_graph_headerlijst_wordt_net_zo_gelezen_als_pop3():
    """POP3 en Graph moeten hetzelfde oordeel geven — anders hangt het van het
    transportpad af of een nieuwsbrief een concept-antwoord krijgt."""
    graph_headers = [{"name": "List-Id", "value": "<nieuws.merk.nl>"}]
    assert bulk.is_bulk(graph_headers, "nieuws@merk.nl", "Aanbieding", "Hallo!")


def test_persoonlijke_mail_is_geen_bulk():
    msg = email.message_from_string(
        "From: jan@klant.nl\nSubject: Vraag over mijn account\n"
        "\nHoi Vincent, hoe kan ik mijn wachtwoord resetten?\n")
    assert bulk.bulk_reason(msg, "jan@klant.nl", "Vraag over mijn account",
                            "Hoi Vincent, hoe kan ik mijn wachtwoord resetten?") is None


# ── 2. Meertalige afmeld-frases (het tekstvangnet) ──────────────────────────

def test_nederlandse_afmeldfrases_tellen_ook():
    """De oude lijst kende alleen 'unsubscribe'; alle vijf de foute concepten
    waren Nederlandstalige mailings met 'uitschrijven' of 'afmelden'."""
    for frase in ("uitschrijven", "afmelden", "je ontvangt deze e-mail omdat"):
        reden = bulk.bulk_reason(None, "hello@mkt.merk.nl", "Deals",
                                 f"Mooie aanbiedingen! ... {frase} ... ")
        assert reden, f"'{frase}' werd niet als afmeld-instructie herkend"


def test_noreply_afzender_is_onbeantwoordbaar():
    assert bulk.is_bulk(None, "no-reply@elevenlabs.io", "Verify your email", "Klik hier")


# ── 3. Woordgrenzen i.p.v. sub-strings ──────────────────────────────────────

def test_wat_matcht_niet_binnen_watersport():
    n, gevonden = bulk.count_words("volop watersport en schoenen", ("wat", "hoe"))
    assert n == 0, f"onterecht gevonden: {gevonden}"


def test_heel_woord_wordt_wel_gevonden():
    n, _ = bulk.count_words("maar wat kost dat dan", ("wat",))
    assert n == 1


def test_leesteken_hint_blijft_letterlijk_werken():
    n, _ = bulk.count_words("kun je dat toelichten?", ("?",))
    assert n == 1


# ── 4. Het gedrag dat er echt toe doet ──────────────────────────────────────

def _nieuwsbrief_body(extra: str = "") -> str:
    return ("Er is zo veel om je op te verheugen. Wat een maand wordt dit! "
            "Hoe je ook reist, wij helpen je. " + extra +
            " Wil je deze mails niet meer? uitschrijven kan hier.")


def test_nieuwsbrief_krijgt_geen_conceptantwoord():
    """Precies het geval-Eurostar: een vraagteken plus 'wat'/'hoe' in een
    marketingtekst leverde het oordeel 'question' op — en dus een concept."""
    kind = classify.classify("Pak de laatste zon mee",
                             _nieuwsbrief_body(), "eurostar@e.eurostar.com")
    assert kind == "newsletter"


def test_nieuwsbriefartikel_wordt_geen_afspraak():
    """Het geval-beehiiv: 'hebben we' uit een artikel van 7000 tekens maakte
    er een afspraak-verzoek van, met een voorstel voor 30 mei 2027."""
    artikel = _nieuwsbrief_body(
        "Wat weet Apple over AI? hebben we het daar al over gehad? "
        "Op 30 mei presenteerde het bedrijf zijn plannen. " * 20)
    kind = classify.classify("Wat Apple over AI weet", artikel,
                             "ai-report@mail.beehiiv.com")
    assert kind == "newsletter"


def test_echte_vraag_van_een_mens_blijft_een_vraag():
    """De gate mag niet zo streng worden dat echte klantvragen sneuvelen."""
    kind = classify.classify(
        "Vraag over mijn bestelling",
        "Hoi, ik kan niet inloggen op mijn account. Hoe reset ik mijn "
        "wachtwoord? Het werkt niet meer sinds gisteren.",
        "jan@klant.nl")
    assert kind == "question"


def test_echt_afspraakverzoek_blijft_een_afspraak():
    kind = classify.classify(
        "Kennismaking",
        "Hoi Vincent, zullen we volgende week dinsdag om 14:00 bellen?",
        "astrid@zorgorganisatie.nl")
    assert kind == "appointment"


def test_bulkheader_wint_van_een_afspraakzin():
    """Ook als de mailing letterlijk 'zullen we' bevat: een verzending is
    geen afspraak-verzoek."""
    graph_headers = [{"name": "List-Unsubscribe", "value": "<https://x.nl/u>"}]
    kind = classify.classify(
        "Zullen we samen op reis?",
        "Zullen we volgende week dinsdag om 14:00 samen op reis gaan?",
        "reizen@mkt.merk.nl", headers=graph_headers)
    assert kind == "newsletter"


# ── 5. De ophaal-gate ziet de body (en dus de footer) ───────────────────────

def test_ophaalgate_herkent_mailing_aan_de_body():
    """Vóór 1 aug 2026 kreeg _should_ignore vanuit fetch_new alleen het
    onderwerp mee (body=""), waardoor de afmeld-footer — het sterkste
    tekstsignaal — op het beslismoment niet bestond."""
    assert _should_ignore("hello@mkt.merk.nl", "Deals van de week",
                          body=_nieuwsbrief_body())


def test_ophaalgate_laat_een_echte_vraag_door():
    assert not _should_ignore(
        "jan@klant.nl", "Vraag over mijn bestelling",
        body="Hoi, wanneer wordt mijn bestelling geleverd?")
