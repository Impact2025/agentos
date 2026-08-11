"""Ruis uit een e-mailbody knippen vóórdat er iets mee wordt beslist.

Twee soorten ruis, allebei "iemand anders' tekst die op de nieuwe boodschap
lijkt geplakt": een geciteerde eerdere mail (Outlook/Gmail-stijl aanhef) en de
handtekening/footer van de afzender (functietitel, telefoonnummer, en steeds
vaker een marketing-CTA als "Boek een afspraak in mijn agenda" of een
Calendly-link). Beide zijn brontekst van iemand anders' bedoeling, niet van de
nieuwe boodschap — een datum in een citaat of een boekingslink in een
handtekening is geen afspraakwens van de afzender aan óns.

Gedeeld tussen `mail/classify.py` (classificatie) en `calendar/agent.py`
(datum/tijd-extractie): twee losse implementaties van "wat is de citaat-grens"
is precies het soort duplicatie waar deze codebase op stuk is gelopen (zie
CLAUDE.md 7a-bis, "twee antwoorden op dezelfde vraag").
"""
import re

# Markeert het begin van een geciteerde eerdere mail (Outlook/Gmail-stijl,
# NL en EN). Alles ná de eerste treffer is iemand anders' oude bericht, niet
# de nieuwe tekst — een datum/tijd/locatie (of een afspraak-signaal) daaruit
# hoort bij de aanhef, niet bij wat de afzender nu schrijft. Gemeten 9 aug
# 2026: een "Sent: ... 20:11:04"-regel in een geciteerde header leverde een
# afspraakvoorstel op om 20:11.
_QUOTE_MARKERS = re.compile(
    r"(-{2,}\s*(oorspronkelijk bericht|original message)\s*-{2,}"
    r"|^\s*van\s*:.{0,120}$"
    r"|^\s*from\s*:.{0,120}$"
    r"|^\s*sent\s*:.{0,120}$"
    r"|^\s*verzonden\s*:.{0,120}$"
    r"|^\s*op .{0,80} schreef .{0,80}\s*:\s*$"
    r"|^\s*on .{0,80} wrote\s*:\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

# Begin van een afscheidsgroet — alles daarna is handtekening/footer: naam,
# functie, telefoonnummer, en bij outreach-achtige afzenders vaak een eigen
# boekingslink. Bewust een vaste lijst i.p.v. "laatste alinea": een korte mail
# zonder groet mag niet stil zijn hele body verliezen.
_SIGNATURE_MARKERS = re.compile(
    r"^\s*(met\s+)?(vriendelijke|hartelijke)\s+groet(en)?\s*[,.]?\s*$"
    r"|^\s*groet(je)?s?\s*[,.]?\s*$"
    r"|^\s*best\s+regards\s*[,.]?\s*$"
    r"|^\s*kind\s+regards\s*[,.]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_quoted_history(body: str) -> str:
    """Alles vanaf de eerste citaat-marker eraf knippen. Geen match → ongewijzigd."""
    m = _QUOTE_MARKERS.search(body or "")
    return body[: m.start()] if m else (body or "")


def strip_signature_block(body: str) -> str:
    """Alles vanaf de eerste afscheidsgroet eraf knippen. Geen match → ongewijzigd.

    Belangrijk voor classificatie: een handtekening als "📆 Boek een afspraak
    in mijn agenda" bevat zowel 'afspraak' als 'agenda' — twee zwakke
    afspraak-hints die zónder deze knip al voldoende zijn om een simpele
    afwijzingsmail als appointment te classificeren (gemeten 10 aug 2026,
    nlvoorelkaar.nl: "Willen we wel ooit..." werd een afspraakvoorstel puur
    door de CTA-regel in de footer).
    """
    m = _SIGNATURE_MARKERS.search(body or "")
    return body[: m.start()] if m else (body or "")


def strip_noise(body: str) -> str:
    """Beide toepassen, in volgorde: eerst het citaat, dan de handtekening
    van wat overblijft (een handtekening ná een citaat komt niet voor, maar
    andersom — een citaat verstopt in een lange handtekening — wel)."""
    return strip_signature_block(strip_quoted_history(body))
