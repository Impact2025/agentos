"""
Prompt-injectie-scan voor autonome instructies.

Julian Goldie (video dcpWn7rh_G8) noemt dit als het ene veiligheidsdetail dat
mensen fout doen: "when you create a scheduled job, the instructions get scanned
for the classic tricks — someone trying to hide a command inside text your agent
reads." AgentOS had dit nog niet expliciet; de bestaande guards (review-gates,
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
  <system>-tags, base64/encoding-omhulsels, "developer mode").
- Case-insensitive, normaliseer eerst whitespace/uppercase zodat "iGnOrE
  PrEvIoUs" en "IGNORE  PREVIOUS" allebei matchen.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List

log = logging.getLogger(__name__)

# Bekende injectie-patronen. Elk item: (naam, regex vlaggen, patroon).
# De patronen zijn bewust breed maar richten zich op prompts die een model
# dwingen zijn instructies te negeren of een andere identiteit aan te nemen.
_PATTERNS: List[tuple] = [
    ("ignore_instructions", re.I,
     r"ignore\s+(all\s+)?(previous|prior|above|the\s+)?(instructions|prompt|context|system)"),
    ("forget", re.I,
     r"(forget|disregard|ignore|discard)\s+(everything|all|previous|prior|above)"),
    ("system_role", re.I,
     r"(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+you\s+are|your\s+new\s+role\s+is|assume\s+the\s+role\s+of)"),
    ("system_tag", re.I,
     r"<system>|</system>|\[system\]|\[INST\]|<<SYS>>"),
    ("developer_mode", re.I,
     r"(developer\s*mode|jailbreak|danmode|jail\s*break|roasting\s+mode|uncensored\s+mode)"),
    ("hidden_command", re.I,
     r"(run\s+this\s+command|execute\s+the\s+following|do\s+exactly\s+as\s+i\s+say|disregard\s+your\s+guidelines)"),
    ("override", re.I,
     r"(bypass|override|disable)\s+(your\s+)?(safety|guardrail|filter|guideline|rule|instruction|policy)"),
    ("new_instruction", re.I,
     r"(new\s+instructions?|updated\s+instructions?|the\s+real\s+instructions?|secret\s+instructions?)"),
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
