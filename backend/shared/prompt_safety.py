"""
Prompt-injectie-scan voor autonome instructies.

Julian Goldie (video dcpWn7rh_G8) noemt dit als het ene veiligheidsdetail dat
mensen fout doen: "when you create a scheduled job, the instructions get scanned
for the classic tricks — someone trying to hide a command inside text your agent
reads." ImpactOS had dit nog niet expliciet; de bestaande guards (review-gates,
"activiteit is geen effect", snapshots) dekken wél de uitvoer-kant, maar niet de
invoer-kant van een doel/delegatie die door een externe bron gevoed wordt.

Deze scanner loopt vóórdat een autonome instructie (goal-objective, worker-goal,
delegate-objective) naar de agentic loop gaat. Hij blokkeert patronen die een
model proberen te dwingen zijn systeem-prompt te negeren of een andere rol aan
te nemen. Een hit stopt de creatie — er draait niets, er wordt niets geschreven.

Ontwerpkeuzes:
- Fail-OPEN is hier gevaarlijk (een gemiste injectie = agent doet wat een
  vreemde zegt). Dus: bij twijfel blokkeren, maar wel met een leesbare melding
  die de gedetecteerde patronen noemt, zodat een mens kan oordelen.
- We scannen NIET op reguliere taal; alleen op de bekende instructie-patronen
  ("ignore previous", "system:", "you are now", "forget everything", verborgen
  <system>-tags, base64/encoding-omhulsels, "developer mode") — en hun
  Nederlandse tegenhangers ("negeer alle instructies", "doe alsof je",
  "vergeet alles hierboven"). ImpactOS is een overwegend Nederlandstalig
  systeem (mail, leads, radar-signalen, content) — een scanner die alleen
  Engels herkent laat precies de tekst door die het systeem dagelijks
  verwerkt.
- Case-insensitive, normaliseer eerst whitespace/uppercase zodat "iGnOrE
  PrEvIoUs" en "IGNORE  PREVIOUS" allebei matchen.
- De scan hoort bij de service-functie die de autonome uitvoering start
  (`goal_service.create_and_plan`, `delegate_service.spawn_delegation`), niet
  bij de HTTP-router eromheen. Een instructie kan het systeem ook binnenkomen
  via de chat-agent zelf (de `delegate`-tool in backend/tools/delegate.py,
  aangeroepen door het LLM op basis van gespreks-/webcontext) of via Iris'
  eigen doel-voorstellen — geen van beide loopt langs een HTTP-route. Een
  guard die alleen bij één deur staat is decoratie zodra er een tweede deur
  is (zie CLAUDE.md 7f: "de guard hoort bij de deur, niet bij de
  aanroepers").
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List

log = logging.getLogger(__name__)


class PromptInjectionDetected(ValueError):
    """Autonome instructie geweigerd wegens een gedetecteerd injectie-patroon.

    Erft van ValueError zodat bestaande `except ValueError` afhandeling in de
    routers (→ HTTP 400) dit automatisch correct afhandelt, zonder dat elke
    aanroeper de scan zelf hoeft te herhalen.
    """

# Bekende injectie-patronen. Elk item: (naam, regex vlaggen, patroon).
# De patronen zijn bewust breed maar richten zich op prompts die een model
# dwingen zijn instructies te negeren of een andere identiteit aan te nemen.
# Engels en Nederlands staan naast elkaar (niet als aparte lijst) zodat één
# hit-naam altijd hetzelfde patroon-type betekent, ongeacht de taal.
_PATTERNS: List[tuple] = [
    ("ignore_instructions", re.I,
     # Let op de \s* tussen het optionele bijvoeglijk naamwoord en het
     # zelfstandig naamwoord: zonder die spatie matcht "previous" nooit
     # gevolgd door "instructions" (twee woorden), alleen "previousinstructions"
     # (één woord) — precies de fout die deze regex bij de eerste versie had,
     # waardoor de meest voor de hand liggende frase ("ignore previous
     # instructions") nooit werd herkend en de scan alleen bij toeval op het
     # bredere 'forget'-patroon terugviel.
     r"ignore\s+(all\s+)?(previous|prior|above|the\s+)?\s*(instructions|prompt|context|system)"
     r"|negeer\s+(alle\s+)?(vorige|voorgaande|bovenstaande|de\s+)?\s*(instructies|opdracht|context|systeem)"),
    ("forget", re.I,
     r"(forget|disregard|ignore|discard)\s+(everything|all|previous|prior|above)"
     r"|(vergeet|negeer)\s+(alles|alle|het\s+voorgaande|het\s+bovenstaande)"),
    ("system_role", re.I,
     r"(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+you\s+are|your\s+new\s+role\s+is|assume\s+the\s+role\s+of)"
     r"|(je\s+bent\s+nu|doe\s+alsof\s+je|speel\s+de\s+rol\s+van|vanaf\s+nu\s+ben\s+je|je\s+nieuwe\s+rol\s+is)"),
    ("system_tag", re.I,
     r"<system>|</system>|\[system\]|\[INST\]|<<SYS>>"),
    ("developer_mode", re.I,
     r"(developer\s*mode|jailbreak|danmode|jail\s*break|roasting\s+mode|uncensored\s+mode)"
     r"|(ontwikkelaarsmodus|ongecensureerde\s+modus)"),
    ("hidden_command", re.I,
     r"(run\s+this\s+command|execute\s+the\s+following|do\s+exactly\s+as\s+i\s+say|disregard\s+your\s+guidelines)"
     r"|(voer\s+dit\s+commando\s+uit|voer\s+het\s+volgende\s+uit|doe\s+precies\s+wat\s+ik\s+zeg|negeer\s+je\s+richtlijnen)"),
    ("override", re.I,
     r"(bypass|override|disable)\s+(your\s+)?(safety|guardrail|filter|guideline|rule|instruction|policy)"
     r"|(omzeil|overschrijf|schakel\s+uit)\s+(je\s+)?(veiligheid|filter|richtlijn|regel|instructie|beleid)"),
    ("new_instruction", re.I,
     r"(new\s+instructions?|updated\s+instructions?|the\s+real\s+instructions?|secret\s+instructions?)"
     r"|(nieuwe\s+instructies?|bijgewerkte\s+instructies?|de\s+echte\s+instructies?|geheime\s+instructies?)"),
]

# Verborgen commando's via encoding-omhulsels — een klassieke "steganografie"
# injectie: een base64- of unicode-omhulsel dat het model gevraagd wordt te
# decoderen en uit te voeren. Niet alles wat base64 líjkt is kwaad, maar een
# expliciete decodeer-opdracht in de buurt van een lange base64-blob is een
# signaal.
_ENCODE_HINT = re.compile(
    r"(decode|decrypt|unescape|base64|rot13|from\s+base64|unicode\s*escape)"
    r"(\s+(and\s+)?(run|execute|eval|print|output)|.{0,30}(run|execute|eval|print))",
    re.I,
)
_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


@dataclass
class ScanResult:
    blocked: bool
    hits: List[str] = field(default_factory=list)
    # De (genormaliseerde) tekst die gescand is, handig voor logging.
    sample: str = ""

    def reason(self) -> str:
        if not self.hits:
            return ""
        return "Possible prompt injection gedetecteerd in instructie: " + \
            ", ".join(sorted(set(self.hits))) + \
            ". Autonome uitvoering geweigerd — controleer de bron."


def scan_instruction(text: str) -> ScanResult:
    """Scan één instructie-tekst op injectie-patronen.

    Returns ScanResult met blocked=True zodra één patroon matcht.
    """
    if not text or not text.strip():
        return ScanResult(blocked=False, sample="")

    raw = text.strip()
    # Normaliseer: collapse whitespace, lowercase voor de vergelijking.
    norm = re.sub(r"\s+", " ", raw).lower()

    hits: List[str] = []
    for name, flags, pattern in _PATTERNS:
        try:
            if re.search(pattern, raw, flags):
                hits.append(name)
        except re.error:
            # Een kapot patroon mag de scan nooit laten crashen — blokkeer
            # defensief zodat een model niet stil doorloopt.
            log.warning("prompt_safety: kapot patroon '%s'", name)
            hits.append(name + "_regex_error")

    # Encoding-omhulsel: expliciete decodeer-opdracht EN een lange base64-blob.
    if _ENCODE_HINT.search(raw) and _BASE64_BLOB.search(raw):
        hits.append("encoded_command")

    result = ScanResult(blocked=bool(hits), hits=hits, sample=raw[:200])
    if result.blocked:
        log.warning("prompt_safety: instructie geblokkeerd — hits=%s", hits)
    return result


def scan_structured(**fields) -> ScanResult:
    """Scan meerdere velden tegelijk (goal objective + titel, worker goals).

    Combineert alle hits; blokkeert als één veld een hit heeft.
    """
    combined_hits: List[str] = []
    blocked = False
    for label, value in fields.items():
        if not value:
            continue
        res = scan_instruction(str(value))
        if res.blocked:
            blocked = True
            for h in res.hits:
                combined_hits.append(f"{label}:{h}")
    return ScanResult(blocked=blocked, hits=combined_hits)


def guard_structured(**fields) -> None:
    """Scan en gooi PromptInjectionDetected bij een hit — anders niets.

    Bedoeld voor de service-functie die de autonome uitvoering start (niet de
    HTTP-route eromheen), zodat élke aanroeper — via de API, via een
    tool-aanroep vanuit de chat-agent, of via een interne caller zoals Iris of
    de strategist — dezelfde gate passeert. Eén implementatie i.p.v. dat elke
    aanroeper `scan_structured` + de HTTPException zelf herhaalt.
    """
    scan = scan_structured(**fields)
    if scan.blocked:
        raise PromptInjectionDetected(scan.reason())
