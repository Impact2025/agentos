"""Knowledge Forge — LLM-extractie van de "brain file".

Vertaalt de video's /learn-belofte naar een concreet, agent-leesbaar artifact:

  1. INDEX       — de hoofdstukken/secties + per sectie wat er staat
  2. GLOSSARY    — sleuteltermen uit het document met een korte definitie
  3. CHEAT SHEET — 5-10 direct toepasbare vuistregels ("quick rules")

Dit is de gestructureerde laag die AgentOS nu MIS (NotebookLM geeft wel RAG
maar bouwt geen glossary/cheat-sheet; iris-knowledge distilleert alleen
principes). De brain file wordt opgeslagen als een aparte vault-note én als
JSON in de DB, zodat agents hem zowel semantisch (embeddings) als
gestructureerd (index/cheat-sheet) kunnen raadplegen.

Robuustheid (copieer het iris-patroon):
- LLM faalt => naïeve extractie uit koppen/lijsten. Nooit lege output.
- Deterministisch waar het kan; geen willekeur.
- Taalneutraal: systeemprompt dwingt NL-antwoord af voor NL-documenten.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _extract_json(raw: str) -> Optional[Dict]:
    """Haal de eerste JSON-object uit een LLM-response (robuust tegen prose)."""
    if not raw:
        return None
    # Probeer direct
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Zoek eerste { ... } blok (genest, niet-greedy op matching haakjes)
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start:i + 1])
                except Exception:
                    return None
    return None


async def extract_brain_file(title: str, chunks: List[dict],
                             llm_call) -> Dict[str, Any]:
    """Bouw de brain file via de LLM.

    ``llm_call(system, prompt, max_tokens) -> str | None`` — injecteer de
    centrale AgentOS-LLM hier (OpenModel/Ollama) zonder die te importeren,
    zodat deze module los testbaar blijft.

    Retourneert altijd een geldige dict met keys:
      index (List[{section, summary}]),
      glossary (List[{term, definition}]),
      cheat_sheet (List[str]),
      llm_used (bool).
    """
    # Bouw een compacte representatie van het document voor de LLM (koppen +
    # eerste zinnen per chunk — niet de hele tekst, dat spaart tokens).
    doc_brief = []
    for c in chunks[:40]:
        head = c.get("heading") or f"(sectie {c['index']})"
        preview = c["text"][:400].replace("\n", " ")
        doc_brief.append(f"### {head}\n{preview}")
    doc_brief_text = "\n\n".join(doc_brief)[:9000]

    schema = {
        "index": [{"section": "naam van hoofdstuk/sectie",
                   "summary": "1 zin wat er in deze sectie staat"}],
        "glossary": [{"term": "sleutelterm uit het document",
                      "definition": "korte, eigen woorden-definitie (1 zin)"}],
        "cheat_sheet": ["5-10 direct toepasbare vuistregels, imperatief en kort"],
    }
    prompt = (
        f"Documenttitel: {title}\n\n"
        "Hieronder staan de secties van het document (kop + korte preview).\n"
        "Bouw drie dingen die een AI-agent kan gebruiken om dit document\n"
        "voortaan 'te kennen' zonder het telkens opnieuw te lezen:\n"
        "1) index: elke genoemde sectie/hfdstuk met 1-zins samenvatting\n"
        "2) glossary: de 5-12 belangrijkste vaktermen met 1-zins definitie\n"
        "3) cheat_sheet: 5-10 direct toepasbare regels (vuistregels)\n\n"
        "Antwoord uitsluitend met JSON in dit schema:\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + f"\n\nSECTIES:\n{doc_brief_text}"
    )
    system = (
        "Je bent een kennis-architect. Je zet een document om in een strakke, "
        "machine-leesbare structuur (index, glossary, cheat-sheet). Geen "
        "vage algemeenheden — alleen wat een agent concreet anders doet. "
        "Schrijf in het Nederlands tenzij het brondocument expliciet Engels is."
    )

    raw = None
    try:
        raw = await llm_call(system, prompt, max_tokens=2000)
    except Exception as e:
        logger.warning("[forge] LLM-call faalde: %s", e)

    parsed = _extract_json(raw) if raw else None
    if parsed and isinstance(parsed, dict) and (
            parsed.get("index") or parsed.get("glossary") or parsed.get("cheat_sheet")):
        return _normalize_brain_file(parsed, llm_used=True)

    # ── Naïeve terugval (geen LLM / parseerfout) ──────────────────────────
    return _naive_brain_file(title, chunks)


def _normalize_brain_file(parsed: Dict, llm_used: bool) -> Dict[str, Any]:
    index = []
    for it in (parsed.get("index") or []):
        if isinstance(it, dict) and (it.get("section") or it.get("summary")):
            index.append({
                "section": str(it.get("section", ""))[:160],
                "summary": str(it.get("summary", ""))[:400],
            })
    glossary = []
    for it in (parsed.get("glossary") or []):
        if isinstance(it, dict) and it.get("term"):
            glossary.append({
                "term": str(it["term"])[:80],
                "definition": str(it.get("definition", ""))[:300],
            })
    cheat = [str(x)[:240] for x in (parsed.get("cheat_sheet") or []) if str(x).strip()]
    return {"index": index, "glossary": glossary,
            "cheat_sheet": cheat[:12], "llm_used": llm_used}


def _naive_brain_file(title: str, chunks: List[dict]) -> Dict[str, Any]:
    """Zonder LLM: bouw index uit koppen, glossary uit hoofdtermen, cheat uit lijsten."""
    index = []
    terms = {}
    cheat = []
    for c in chunks:
        head = c.get("heading") or f"(sectie {c['index']})"
        if head:
            index.append({"section": head[:160],
                          "summary": c["text"][:200].replace("\n", " ").strip()[:400]})
        # glossary-kandidaten: "Term — definitie" of "Term: definitie"
        for m in re.finditer(r"^([A-ZËÉÓÖÀÁÂ][\w\-]{2,40})[\s ]*[:—-]\s*(.+)$",
                             c["text"], re.MULTILINE):
            term, definition = m.group(1), m.group(2).strip()
            if 3 <= len(term) <= 60 and len(definition) > 10:
                terms[term] = definition[:300]
        # cheat: geprojecteerde lijst-items en korte gebiedende zinnen
        for line in c["text"].splitlines():
            stripped = line.strip()
            is_bullet = stripped.startswith(("- ", "* ", "• ")) or re.match(r"^\d+\.\s", stripped)
            # inhoud zónder de bullet-marker, voor een leesbare cheat-regel
            content = re.sub(r"^([-*•]\s+|\d+\.\s+)", "", stripped).strip()
            if is_bullet and 15 < len(content) < 160:
                cheat.append(content[:240])
    glossary = [{"term": t, "definition": d} for t, d in list(terms.items())[:12]]
    return {"index": index[:40], "glossary": glossary,
            "cheat_sheet": cheat[:12], "llm_used": False}
