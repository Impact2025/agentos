"""Knowledge Forge — document readers + structure-aware chunking.

Leest PDF / DOCX / MD / TXT / URL (web) naar platte tekst, en hakt die
daarna op in chunks die de *structuur* van het document volgen (hoofdstukken,
secties, paragrafen) in plaats van domme vaste karaktervensters. Dat is het
verschil tussen "Hermes /learn" (die belooft per-hoofdstuk toegang) en een
primitieve RAG: de index verwijst naar echte secties, niet naar willekeurige
snippets.

Dependency-vrij waar het kan:
- PDF: we proberen pypdf (als geïnstalleerd), anders een ingebouwde
  minimalistische tekstextractie uit de PDF-stream (werkt voor de meeste
  doorzoekbare PDF's; gescande PDF's zonder tekstlaag leveren een lege string
  — net als de iris-knowledge lezer).
- DOCX: zipfile + xml parsing (geen python-docx nodig).
- MD/TXT: rechtstreeks.
- URL: httpx fetch + BeautifulSoup (al in requirements).

Alles defensief: een leesfout levert "" op, nooit een crash — ingest mag
nooit stuklopen op één rotbestand.
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


# ── Low-level extractors ─────────────────────────────────────────────────────

def _read_pdf_native(path: Path) -> str:
    """Minimale PDF-tekstextractie zónder externe libs.

    PDF's bewaren tekst meestal als ``(...) Tj`` of ``[...] TJ`` operatoren
    binnen content streams. We pakken de strings tussen die operatoren en
    plakken ze aan elkaar. Geen layout, géén font-mapping — maar voor
    retrieval en LLM-distillatie is de ruwe tekst voldoende. Voor
    productiekwaliteit (kolommen, ligaturen) installeer je pypdf; die wordt
    hieronder met voorrang gebruikt.
    """
    try:
        data = path.read_bytes()
        text = data.decode("latin-1", errors="ignore")
        # Pak strings uit (...) Tj  en [ ... ] TJ
        chunks = []
        for m in re.finditer(r"\((?:[^()\\]|\\.)*\)\s*Tj", text):
            s = m.group(0)[1:m.group(0).index(")")]
            chunks.append(_pdf_unescape(s))
        for m in re.finditer(r"\[(?:[^\[\]]|\\.)*\]\s*TJ", text):
            inner = m.group(0)[1:m.group(0).index("]")]
            # haal losse strings uit de array
            for sm in re.finditer(r"\((?:[^()\\]|\\.)*\)", inner):
                chunks.append(_pdf_unescape(sm.group(0)[1:-1]))
        out = " ".join(c for c in chunks if c.strip())
        return re.sub(r"\s{2,}", " ", out).strip()
    except Exception as e:
        logger.debug("[forge-reader] PDF-native mislukt %s: %s", path.name, e)
        return ""


def _pdf_unescape(s: str) -> str:
    s = s.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    s = s.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    return s


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # voorkeur als beschikbaar
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        # pypdf kan een "lege" extractie geven (gescande PDF zonder tekstlaag,
        # of een kapotte stream) — val dan terug op de native extractor.
        if len(text) <= 40:
            native = _read_pdf_native(path)
            if len(native) > len(text):
                return native
        return text
    except Exception:
        pass  # val terug op native
    return _read_pdf_native(path)


def _read_docx(path: Path) -> str:
    """DOCX = zip met word/document.xml. Parse zonder python-docx."""
    try:
        with zipfile.ZipFile(str(path)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
        # <w:t>...</w:t> bevat tekst; <w:p> en <w:br/> zijn newline's
        xml = xml.replace("</w:p>", "\n").replace("</w:br>", "\n")
        parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.DOTALL)
        text = "".join(parts)
        # decodeer XML-entiteiten
        text = (text.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"')
                    .replace("&apos;", "'").replace("&#", "&#"))
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception as e:
        logger.warning("[forge-reader] DOCX lezen mislukt %s: %s", path.name, e)
        return ""


def _read_url(url: str) -> str:
    try:
        import httpx
        from bs4 import BeautifulSoup
        r = httpx.get(url, timeout=20,
                     headers={"User-Agent": "AgentOS-KnowledgeForge/1.0"})
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        # Voorkeur: <article>, anders <main>, anders body
        node = soup.find("article") or soup.find("main") or soup.body
        if node is None:
            return soup.get_text(" ", strip=True)
        return re.sub(r"\s{2,}", " ", node.get_text(" ", strip=True)).strip()
    except Exception as e:
        logger.warning("[forge-reader] URL lezen mislukt %s: %s", url, e)
        return ""


def read_document(source: str) -> Tuple[str, str]:
    """Lees een bron naar (titel, platte_tekst).

    ``source`` kan een pad zijn (.pdf/.docx/.md/.txt) of een http(s)-URL.
    Geeft (titel, tekst) terug; bij een leesfout is tekst "".
    """
    if source.startswith("http://") or source.startswith("https://"):
        text = _read_url(source)
        title = source.split("/")[-1] or source
        return title, text

    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return path.stem, _read_pdf(path)
    if suffix == ".docx":
        return path.stem, _read_docx(path)
    if suffix in (".md", ".txt", ".markdown"):
        try:
            raw = path.read_text("utf-8", errors="ignore")
        except Exception:
            return path.stem, ""
        if suffix == ".md":
            raw = _strip_frontmatter(raw)
        return _title_from(raw, path.stem), raw.strip()
    # onbekend: probeer als platte tekst
    try:
        return path.stem, path.read_text("utf-8", errors="ignore").strip()
    except Exception:
        return path.stem, ""


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        idx = text.find("---", 3)
        if idx > 0:
            return text[idx + 3:].strip()
    return text


def _title_from(text: str, fallback: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()[:160]
        if s and not s.startswith("#"):
            return s[:160]
    return fallback


# ── Structure-aware chunking ─────────────────────────────────────────────────

# Heuristiek voor kop-regels (NL/EN): "# Titel", "Hoofdstuk 3 — Naam",
# "3.1 Subsectie", "SECTIE: x", of hoofdletter-regels korter dan 80 tekens.
_HEADING_RE = re.compile(
    r"^(#{1,3}\s+.+|"                      # markdown headings
    r"(hoofdstuk|chapter|deel|part|sectie|section)\s+[\dIVXLC]+\b.*|"  # hoofdstuk/sectie
    r"\d{1,2}(\.\d{1,3})*\s+[A-ZËÉÓÖÀÁÂ].{0,70}|"  # "3.1 Titel"
    r"[A-ZËÉÓÖÀÁÂ][A-ZËÉÓÖÀÁÂ \d\/]{4,70})$"       # HOOFDLIJN
    r"",
    re.IGNORECASE,
)


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90:
        return False
    return bool(_HEADING_RE.match(s))


def split_into_chunks(text: str, max_chars: int = 1400,
                      min_chars: int = 200) -> List[dict]:
    """Hak de tekst op in structurele chunks.

    Elke chunk krijgt:
      - ``heading``: de dichtstbijzijnde kop erboven (of "")
      - ``index``: volgnummer
      - ``text``: de chunk-tekst (kop inbegrepen)

    Logica: we lopen regel voor regel. Bij een kop beginnen we een nieuwe
    sectie. Binnen een sectie bouwen we paragrafen op tot ~max_chars, dan
    breken we (maar nooit midden in een zin als het te vermijden valt).
    Zo blijft elke chunk binnen één onderwerp — retrieval geeft
    sectie-relevante context, niet willekeurige snippets.
    """
    lines = text.splitlines()
    chunks: List[dict] = []
    current_heading = ""
    buf: List[str] = []
    char_count = 0

    def flush():
        nonlocal buf, char_count, current_heading
        if not buf:
            return
        body = "\n".join(buf).strip()
        if len(body) < min_chars and chunks:
            # te kort: voeg toe aan de vorige chunk
            chunks[-1]["text"] = (chunks[-1]["text"] + "\n\n" + body).strip()
        else:
            chunks.append({
                "heading": current_heading,
                "index": len(chunks),
                "text": body,
            })
        buf = []
        char_count = 0

    for line in lines:
        s = line.strip()
        if _is_heading(s):
            # kop wisselt: flush wat we hebben, start nieuwe sectie
            flush()
            current_heading = s
            continue
        if not s:
            continue
        # tel tekstlengte; breek bij overschrijding van max_chars
        if char_count + len(s) > max_chars and buf:
            flush()
        buf.append(s)
        char_count += len(s) + 1

    flush()

    # Index opnieuw nummeren (flush kan samengevoegd hebben)
    for i, c in enumerate(chunks):
        c["index"] = i
    return chunks
