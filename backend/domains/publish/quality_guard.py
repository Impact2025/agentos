"""quality_guard.py — Pre-publish content validator voor Impact OS.

Gespiegeld aan de TypeScript `quality-guard.ts` in de Bijeen-app, zodat de
automation (Iris) dezelfde controles doet vóórdat ze pusht naar de site. Doel:
voorkom dat taalcorruptie (LLM-tokenrot: "without"/"nichts"/CJK), niet-ingevulde
plaatshouders ("[link naar toolkit]") of zichtbare "undefined"/"null"-tekst
oophopend op de live site belanden.

Werking:
- `check(html)` retourneert (ok: bool, issues: list[str], suspicion: int).
- Harde fout → pipeline weigert de publish (en probeert daarna auto-herstel via
  `_auto_repair`, zie content_pipeline-integratie).

Conservatief ontwerp: liever één valse positief (post moet opnieuw) dan één
stukje "nichts" live.
"""
from __future__ import annotations

import re
import unicodedata

# ── 1. Bekende corruptie-tokens (exact, hoofdletterongevoelig) ──────────────
# Alleen woorden die in correct Nederlands nooit voorkomen en typisch zijn voor
# LLM-tokenrot (EN/DE/ZH/JA). Geen gewone leenwoorden ("tool", "team"…).
CORRUPTION_TOKENS = {
    # Engels dat in NL-tekst hier fout zit
    "without", "nichts", "today", "therefore", "however", "moreover", "thus",
    "hence", "whereas", "namely", "indeed", "furthermore", "amongst", "whilst",
    "utilize", "regarding", "additionally", "subsequently", "nonetheless",
    "nevertheless", "overall", "specifically", "approximately", "currently",
    "various", "numerous", "within", "upon", "via", "per", "the", "and",
    "for", "with", "this", "that", "from", "your", "you", "are", "was",
    "were", "will", "have", "has", "been", "they", "their", "there", "here",
    "what", "when", "where", "which", "while", "about", "into", "also", "can",
    "should", "would", "could", "may", "might", "each", "other", "than", "then",
    "them", "these", "those", "some", "such", "only", "just", "like", "more",
    "most", "very", "first", "last", "next", "new", "old", "good", "best",
    "well", "how", "why", "who", "whom", "whose", "our", "out", "use", "used",
    "using", "make", "made", "find", "found", "need", "needs", "one", "two",
    "see", "saw", "get", "got", "let", "set", "end", "big", "small", "high",
    "low", "long", "short", "open", "close", "read", "write", "help", "start",
    "stop", "keep", "give", "take", "work", "play", "show", "turn", "move",
    "live", "dead", "free", "full", "half", "both", "all", "any", "few", "own",
    "same", "real", "sure", "easy", "hard", "fast", "slow", "early", "late",
    # Duits
    "nicht", "und", "oder", "aber", "doch", "sehr", "auch", "schon", "noch",
    "wird", "werden", "seine", "ihre", "einem", "einen", "dieser", "jeder",
    "während", "obwohl", "weil", "dass", "eine", "einer", "kein", "keine",
    "nichts", "alles", "wir", "sie", "er", "es", "ist", "sind", "war", "waren",
    "hat", "haben", "kann", "soll", "muss", "will", "zum", "zur", "auf", "aus",
    "mit", "nach", "bei", "vor", "für", "gegen", "durch", "über", "unter",
    "zwischen", "ohne", "um", "an", "in", "im", "am", "den", "der", "dem",
    "des", "das", "die", "nur", "mehr", "weniger", "ganz",
    # CJK-zinsdelen die als tokenrot verschijnen
    "工具", "数据", "用户", "系统", "问题", "内容", "使用", "请", "我们", "您的",
    "の", "は", "を", "に", "が", "と", "です", "ます", "例えば",
}

# ── 1b. Woorden die in het Nederlands (of als leenwoord) wél geldig zijn ──
# Deze mogen NOOIT als "tokenrot" worden flagt, ook niet als ze toevallig in
# CORRUPTION_TOKENS staan. Zonder deze set falen normale NL-zinnen
# ("er is", "in de", "die man", "het was", "alles half open") ten onrechte.
DUTCH_SAFE = {
    # Nederlandse functiewoorden / lidwoorden / voornaamwoorden / voorzetsels
    "de", "het", "een", "en", "van", "in", "op", "aan", "met", "voor", "naar",
    "bij", "door", "over", "onder", "tussen", "zonder", "om", "tot", "als",
    "dat", "dit", "deze", "ons", "jij", "hij", "zij", "ze", "wij", "ik", "mij",
    "je", "jou", "u", "uw", "zijn", "haar", "hen", "hun", "waar", "wanneer",
    "hoe", "waarom", "welke", "elke", "alle", "veel", "weinig", "sommige",
    "andere", "zelfde", "zelf", "echt", "zeker", "makkelijk", "moeilijk",
    "snel", "langzaam", "vroeg", "laat", "groot", "klein", "hoog", "laag",
    "lang", "kort", "levend", "dood", "gratis", "vol", "beide", "enige",
    "eigen",
    # de specifieke false-positives uit de ochtendrapportage van 18-08
    "er", "was", "waren", "alles", "per", "half", "open", "start", "let",
    # 19 aug 2026: "die" is één van de meest gebruikte NL-woorden (aanwijzend
    # voornaamwoord), maar stond nog niet in DUTCH_SAFE — false-positive die
    # legitieme artikelen (zie "De boodschap blijft hangen of verdwijnt", score
    # 92) onterecht weigerde. Nu toegevoegd.
    "die",
    # Leenwoorden / integrationsvormen die in NL-tekst voorkomen:
    # "play" in "LEGO Serious Play", "team"/"tool" zijn hier al via de globale
    # Engelse set gedekt, maar "play" is niet in CORRUPTION_TOKENS — dit is een
    # geval dat de contamination alleen telt als het woord DAADWERLIJK in
    # CORRUPTION_TOKENS zit. "play" zit er niet in, dus hoeft die safe is.
    # We sluiten toch alle bekende leenwoorden uit voor de threshold-check.
    "via", "see", "get", "set", "end", "out", "use", "used", "using", "make",
    "made", "find", "found", "need", "needs", "one", "two", "you", "your",
    "are", "have", "has", "been", "they", "their", "there", "here", "what",
    "when", "where", "which", "while", "about", "into", "also", "can",
    "should", "would", "could", "may", "might", "each", "other", "than",
    "then", "them", "these", "those", "some", "such", "only", "just", "like",
    "more", "most", "very", "first", "last", "next", "new", "old", "good",
    "best", "well", "how", "why", "who", "whom", "whose", "our", "from",
    "this", "that", "with", "for", "and", "the", "a", "an", "to", "of", "at",
    "is", "it", "as", "be", "do", "we", "he", "she", "my", "me", "not", "no",
    "so", "up", "by", "or", "if", "his",
}

# ── 2. Plaatshouders die nooit live mogen ─────────────────────────────────
PLACEHOLDER_PATTERNS = [
    (re.compile(r"\[link\b[^\]]*\]", re.I), "lege download-/link-plaatshouder '[link …]'"),
    (re.compile(r"\[contact[^\]]*\]", re.I), "e-mail-plaatshouder '[contact …]' (gebruik een echte mailto-link)"),
    (re.compile(r"\[todo\]", re.I), "TODO-plaatshouder"),
    (re.compile(r"\[tbd\]", re.I), "TBD-plaatshouder"),
    (re.compile(r"lorem ipsum", re.I), "'lorem ipsum'-vulling"),
    (re.compile(r"\[\s*(invul|vul hier|hier invullen)[^\]]*\]", re.I), "oningevelde invul-plaatshouder"),
    (re.compile(r"\{\{[^\}]+\}\}"), "onverwerkte template-variabele '{{ … }}'"),
    (re.compile(r"\{\s*\w+\s*\}"), "onverwerkte template-variabele '{ … }'"),
]

# Zichtbaar "undefined"/"null" als tekst
VISIBLE_GARBAGE = re.compile(r"\b(?:undefined|null)\b", re.I)

# Niet-Latijnse scripts (CJK / Cyrillisch / Arabisch / Thai / Grieks)
NON_LATIN_SCRIPT = re.compile(
    r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
    r"\u0400-\u04ff\u0500-\u052f\u0600-\u06ff\u0e00-\u0e7f\u1f00-\u1fff]"
)


def _to_text(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text)


def _tokens(text: str):
    return [t for t in re.split(r"[^a-z0-9à-ÿ]+", text.lower()) if t]


# Drempel: pas falen op vreemde-woord-contaminatie als >= 12% van de tokens
# een ÉCHT vreemd woord is (na uitsluiting van DUTCH_SAFE). Eén los Engels
# zinnetje in een NL-artikel (ratio < drempel) mag de publicatie niet killen;
# structurele rot (hoog ratio) wel. DUTCH_SAFE voorkomt false-positives op
# woorden die in het Nederlands geldig zijn ("er", "in", "die", "was"…).
CONTAMINATION_THRESHOLD = 0.12
# Minimale aantal échte vreemde tokens voordat de percentage-drempel een rol
# speelt. 19 aug 2026: één "die" in een 1147-token artikel (0.17%) maakte
# eerdere versies van deze check faalden met een verkeerde false-positive,
# omdat de code enkel op ratio controleerde zonder een absolute ondergrens.
# Met deze lage drempel moet er echt een structuur van vreemde woorden zijn.
MIN_FOREIGN_TOKENS = 3


def check(html: str):
    """Retourneert (ok, issues, suspicion_score).

    suspicion_score = 0..100: het percentage tokens dat een écht vreemd
    woord bevat (na uitsluiting van DUTCH_SAFE). 0 = schone NL-tekst.
    """
    issues: list[str] = []
    text = _to_text(html)
    toks = _tokens(text)
    n = len(toks) or 1

    foreign = {t for t in toks if t in CORRUPTION_TOKENS and t not in DUTCH_SAFE}
    contamination = len(foreign) / n

    # 19 aug 2026: exige één minimale ABSOLUTE telling van vreemde tokens
    # naast het percentage. Zonder deze ondergrens falen korte artikelen
    # onterecht (één "die" = 0.17% < 12%, maar eerdere versies flagden elke hit).
    # Nu moet er echt structurele contaminatie zijn (>=3 woorden ÍN én >=12%).
    if len(foreign) >= MIN_FOREIGN_TOKENS and contamination >= CONTAMINATION_THRESHOLD:
        sample = sorted(foreign)[:8]
        issues.append(
            f"Mogelijke taalcorruptie (LLM-tokenrot) gedetecteerd: "
            f"{', '.join(sample)}{' …' if len(foreign) > 8 else ''}. "
            "Nederlandse tekst mag geen Engelse/Duitse/zakelijke vreemde woorden bevatten."
        )

    non_latin = NON_LATIN_SCRIPT.search(text)
    if non_latin:
        issues.append(
            "Niet-Latijnse karakters gevonden (Chinees/Japans/Cyrillisch/Arabisch). "
            "Tekst is niet-Nederlands en hoort niet in deze post."
        )

    for rx, msg in PLACEHOLDER_PATTERNS:
        m = rx.search(html) or rx.search(text)
        if m:
            issues.append(f"Plaatshouder niet ingevuld: {msg}.")
            break

    if VISIBLE_GARBAGE.search(text):
        m = VISIBLE_GARBAGE.search(text)
        issues.append(
            f"Zichtbare '{m.group(0)}'-tekst in de body. Template niet correct verwerkt."
        )

    return (len(issues) == 0, issues, round(contamination * 100))


def sanitize_slug(slug: str) -> str:
    """Spiegelt sanitizeSlug() uit de app: & → en, 'amp' → en, dubbele/-trailing dashes."""
    s = slug.replace("&amp;", "en").replace("&", "en")
    s = re.sub(r"amp", "en", s, flags=re.I)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s[:80]


# Auto-herstel: laat een LLM de rot eruit halen. Wordt in content_pipeline
# aangeroepen bij een harde fout, vóórdat de publish definitief wordt geweigerd.
_AUTO_REPAIR_PROMPT = (
    "Je bent een nauwkeurige Nederlandse eindredacteur. Hieronder staat HTML "
    "van een Nederlandstalig blogartikel dat beschadigd is door een "
    "generatiefout. Herstel het ZONDER de betekenis of structuur te veranderen:\n"
    "- Vervang alle Engelse/Duitse/Chinese/Japanse woorden of zinsdelen door "
    "het correcte Nederlands.\n"
    "- Vul geen placeholders in met verzonnen data; verwijder lege "
    "plaatshouders ('[link …]', '[contact …]', '{{ … }}', 'undefined', 'null') "
    "volledig uit de tekst.\n"
    "- Houd de HTML-structuur (koppen, paragrafen, tabellen, lijsten) intact.\n"
    "Geef ALLEEN het gecorrigeerde HTML terug, geen uitleg."
)

# Voorkom circulaire import: content_pipeline importeert ons, wij roepen pas
# lazy aan.
def auto_repair(html: str, llm_fn=None) -> str | None:
    """Probeer de content te herstellen via llm_fn(system, prompt) -> str.
    Retourneert de gecorrigeerde HTML of None bij mislukking."""
    if llm_fn is None:
        return None
    try:
        fixed = llm_fn(_AUTO_REPAIR_PROMPT, html)
        if fixed and fixed.strip() and fixed.strip() != html.strip():
            ok, _, _ = check(fixed)
            if ok:
                return fixed
    except Exception:
        pass
    return None
