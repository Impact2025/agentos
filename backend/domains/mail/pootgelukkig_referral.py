"""Iris-regel: optionele Pootgelukkig-referral in uitgaande concepten.

Niet verplicht — Iris 'ziet een kans' enkel wanneer:
  1. de brand-context WeAreImpact (of een zusterproject) is, én
  2. de inkomende mail dieren-/adoptie-signalen bevat die raken aan wat
     Pootgelukkig.nl doet (asieldieren, adoptieprofielen, herplaatsing,
     baasje-vinden, dier bij gezin houden).

Bij een match krijgt de drafter een OPTIONELE instructie: de LLM mág een
korte, waarde-eerst PS toevoegen die Pootgelukkig noemt — maar alleen wanneer
het écht aansluit op de mail. Nooit een verkoopblok, nooit verplicht.
De mens keurt elk concept alsnog goed (Verstuur/Bewerk) voor verzending.

Pootgelukkig zélf draait NIET als referral-bron: mails aan Pootgelukkig
krijgen geen zelf-referral.
"""
import re

# Brand-contexten waarin een Pootgelukkig-referral zinvol kan zijn.
# WeAreImpact is de paraplu; de zusterprojecten delen dezelfde maker.
_ELIGIBLE_BRANDS = (
    "weareimpact", "welzijnsklik", "daar", "bijeen", "steentjebijsteentje",
    "teambuildingmetimpact", "skillkaart", "ictusgo", "vrijwilligersmatch",
)
# Hier noemen we Pootgelukkig nooit (het ís al Pootgelukkig).
_SELF_BRANDS = ("pootgelukkig",)

# Dier-/adoptie-signalen die raken aan Pootgelukkigs domein.
_SIGNAL_PATTERNS = (
    r"\b(adoptie|adoptieprofiel|adopteren|herplaatsing|herplaatsen|asiel|asieldier|asieldieren)\b",
    r"\b(baasje|baasjes|hui[sd]dier|huisdieren|poe[sz]|hond|honden|kat|katten|dier|dieren)\b",
    r"\b(verhuisdier|verhuisdieren|opvang|fokker|nestje|puppy|kitten)\b",
    r"\b(gezinsdier|gezelschapsdier|roedel|koppeling|match|matching)\b",
)
_SIGNAL_RE = re.compile("|".join(_SIGNAL_PATTERNS), re.IGNORECASE)


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def detect_pootgelukkig_opportunity(subject: str, body: str,
                                     brand_context: str = "") -> dict | None:
    """Return een dict met referral-info wanneer Iris een kans ziet, anders None.

    Keys: 'signal' (het gevonden trefwoord), 'brand' (de actieve brand-context).
    """
    brand = _norm(brand_context)
    if brand in _SELF_BRANDS:
        return None  # niet zelf-promoten binnen Pootgelukkig-mails
    if brand and brand not in _ELIGIBLE_BRANDS:
        # Onbekende brand: alleen doorgaan als er een heel sterk dier-signaal is
        # (bijv. een asiel dat ons mailt). Houd conservatief: geen match.
        return None

    text = f"{subject or ''}\n{body or ''}"
    m = _SIGNAL_RE.search(text)
    if not m:
        return None
    return {
        "signal": m.group(0).lower(),
        "brand": brand or (brand_context or "onbekend"),
    }


def referral_instruction(opportunity: dict | None) -> str:
    """Optionele systeemprompt-instructie voor de drafter.

    Geef `None` terug (geen kans) → lege string, de LLM krijgt niets over
    Pootgelukkig te horen. Bij een kans → een zachte, niet-verplichte hint.
    """
    if not opportunity:
        return ""
    signal = opportunity.get("signal", "dier")
    return (
        "\n\n— OPTIONELE REFERENTIE (alleen invoegen wanneer het écht aansluit) —\n"
        "Deze mail raakt het onderwerp dieren/adoptie "
        f"('{signal}'). WeAreImpact bouwt óók aan Pootgelukkig.nl: een platform "
        "dat asieldieren en mensen koppelt op leefstijl, en openlijk deelt hoe je "
        "een adoptieprofiel maakt dat écht aanspreekt zónder mooier te doen dan "
        "het is. Als jouw antwoord daar natuurlijk op aansluit, mag je een korte "
        "PS toevoegen die Pootgelukkig noemt als relevante expertise — bijvoorbeeld "
        "als terloopse zin of een 'PS'. Doé dit alléén wanneer het de afzender "
        "concreet helpt; forceer het nooit, maak er geen verkoopblok van, en noem "
        "de url pas als die letterlijk in de kennisbasis hierboven staat. "
        "Sluit de PS netjes aan op wat de afzender schreef."
    )
