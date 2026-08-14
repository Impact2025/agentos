"""quality_guard.py — Pre-publish content validator voor Agent OS.

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


def check(html: str):
    """Retourneert (ok, issues, suspicion_score)."""
    issues: list[str] = []
    text = _to_text(html)
    toks = _tokens(text)

    hits = {t for t in toks if t in CORRUPTION_TOKENS}
    if hits:
        sample = sorted(hits)[:8]
        issues.append(
            f"Mogelijke taalcorruptie (LLM-tokenrot) gedetecteerd: "
            f"{', '.join(sample)}{' …' if len(hits) > 8 else ''}. "
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

    return (len(issues) == 0, issues, len(hits))


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
