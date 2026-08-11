"""Iris' kennisbank — Vincent voedt Iris met onderzoek, zij wordt er slimmer van.

Je dropt markdown-onderzoek (bijv. Generative Engine Optimization, AEO-tactieken,
merkstrategie) in de vault-map `Iris_Kennisbank/`. Iris leest elk bestand,
distilleert het via de LLM tot een korte samenvatting + een handvol toepasbare
principes met tags (geo/seo/content/...) en een scope (alle projecten of één),
en gebruikt die daarna op twee plekken:

1. in haar dagelijkse analyse-prompt — zodat haar oordeel en advies de nieuwste
   kennis weerspiegelen;
2. in de content-schrijfprompts — zodat de agents die ze aanstuurt de kennis
   daadwerkelijk tóepassen.

Alles is defensief: geen vault = geen actie; geen LLM = een naïeve distillatie
(kopjes/opsommingen als principes) zodat kennis nooit verloren gaat.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...shared.config import OBSIDIAN_VAULT_PATH
from ...shared.database import get_conn

logger = logging.getLogger(__name__)

KNOWLEDGE_DIRNAME = "Iris_Kennisbank"
_MAX_PRINCIPLES_IN_PROMPT = 24
_README = """# Iris Kennisbank

Drop hier markdown-bestanden met onderzoek, tactieken of strategie die je wilt
dat **Iris** (de manager-agent van Agent OS) leert en toepast.

Voorbeelden: Generative Engine Optimization (GEO), AEO/AI-Overviews, SEO-updates,
merkrichtlijnen, doelgroep-inzichten.

Zo werkt het:
- Markdown (.md) of PDF (.pdf) — beide worden gelezen. Bij .md: geef een
  duidelijke titel (H1) bovenaan; bij PDF wordt de bestandsnaam de titel.
- Eén onderwerp per bestand.
- Wil je kennis voor één project? Zet dat in de tekst (bijv. "Alleen voor WeAreImpact").
- Iris leest deze map elke ochtend (06:45) en na een druk op "Ververs kennis"
  in het dashboard. Ze distilleert elk bestand tot toepasbare principes.

Wat je hier neerlegt, stuurt zowel Iris' advies als de content-agents aan.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _folder() -> Optional[Path]:
    if not OBSIDIAN_VAULT_PATH:
        return None
    root = Path(OBSIDIAN_VAULT_PATH)
    if not root.is_dir():
        return None
    return root / KNOWLEDGE_DIRNAME


def ensure_folder() -> Optional[str]:
    """Maak de kennisbank-map (met README) aan als de vault bestaat."""
    folder = _folder()
    if folder is None:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        readme = folder / "_LEESMIJ.md"
        if not readme.exists():
            readme.write_text(_README, encoding="utf-8")
    except Exception as e:
        logger.warning("[iris-knowledge] map aanmaken mislukt: %s", e)
        return None
    return str(folder)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        idx = text.find("---", 3)
        if idx > 0:
            return text[idx + 3:].strip()
    return text


def _read_pdf(path: Path) -> str:
    """Extraheer platte tekst uit een PDF. Defensief: lukt het niet, lege string."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts)
        # Overtollige witruimte opschonen — PDF-extractie levert veel losse regels.
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception as e:
        logger.warning("[iris-knowledge] PDF lezen mislukt (%s): %s", path.name, e)
        return ""


def _read_doc(path: Path) -> str:
    """Lees de tekst uit een kennisbestand (.md of .pdf)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    raw = path.read_text("utf-8", errors="ignore")
    return _strip_frontmatter(raw).strip()


def _title_from(text: str, fallback: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()[:120]
        if s and not s.startswith("#"):
            return s[:120]
    return fallback


# ── Distillatie (LLM, met naïeve terugval) ──────────────────────────────────

async def _distill(title: str, body: str) -> Dict[str, Any]:
    """Distilleer een onderzoeksdoc tot {summary, principles[], tags[], scope}."""
    from .service import _llm, _extract_json

    prompt = (
        "Je krijgt onderzoek/kennis die de manager-agent Iris moet toepassen bij "
        "SEO/content-beslissingen. Distilleer het tot concreet toepasbare kennis. "
        "Antwoord uitsluitend met JSON:\n"
        + json.dumps({
            "samenvatting": "2-3 zinnen kern",
            "principes": ["4-8 concrete, toepasbare vuistregels (imperatief, kort)"],
            "tags": ["bv. geo, seo, content, aeo, merk"],
            "scope": "all of een exacte projectnaam als de kennis maar voor één project geldt",
        }, ensure_ascii=False, indent=2)
        + f"\n\nTitel: {title}\n\nInhoud:\n{body[:6000]}"
    )
    raw = await _llm(
        "Je bent een SEO/AI-search-expert die onderzoek omzet in scherpe, "
        "toepasbare vuistregels. Geen algemeenheden — alleen wat een agent "
        "concreet anders laat doen.",
        prompt, max_tokens=1500,
    )
    parsed = _extract_json(raw) if raw else None
    if parsed:
        return {
            "summary": (parsed.get("samenvatting") or "")[:600],
            "principles": [str(p)[:240] for p in (parsed.get("principes") or [])][:10],
            "tags": [str(t).strip().lower()[:24] for t in (parsed.get("tags") or [])][:8],
            "scope": (parsed.get("scope") or "all").strip()[:60] or "all",
        }
    # Terugval zonder LLM: kopjes en opsommingen als principes, eerste alinea als
    # samenvatting. Kennis mag nooit verloren gaan omdat een model even weg is.
    principles: List[str] = []
    summary = ""
    for line in body.splitlines():
        s = line.strip()
        if not summary and s and not s.startswith("#"):
            summary = s[:600]
        if s.startswith(("- ", "* ", "## ", "### ")):
            cleaned = s.lstrip("-*# ").strip()
            if len(cleaned) > 8:
                principles.append(cleaned[:240])
    return {"summary": summary, "principles": principles[:10], "tags": [], "scope": "all"}


def _upsert(source: str, source_path: str, title: str, content_hash: str,
            distilled: Dict[str, Any]) -> str:
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM iris_knowledge WHERE source_path = ? AND source = ?",
            (source_path, source),
        ).fetchone() if source_path else None
        vals = (title[:200], content_hash, distilled["summary"],
                json.dumps(distilled["principles"], ensure_ascii=False),
                json.dumps(distilled["tags"], ensure_ascii=False),
                distilled["scope"], now)
        if row:
            conn.execute(
                "UPDATE iris_knowledge SET title=?, content_hash=?, summary=?, "
                "principles=?, tags=?, scope=?, active=1, updated_at=? WHERE id=?",
                vals + (row["id"],),
            )
            return row["id"]
        kid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO iris_knowledge (id, source, source_path, title, content_hash, "
            "summary, principles, tags, scope, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (kid, source, source_path) + vals[:1] + (content_hash,) + vals[2:6] + (now, now),
        )
        return kid


async def sync_knowledge() -> Dict[str, Any]:
    """Scan de vault-map, distilleer nieuwe/gewijzigde docs, ruim verdwenen op."""
    folder = _folder()
    if folder is None:
        return {"ok": False, "reason": "geen vault geconfigureerd", "ingested": 0}
    ensure_folder()

    seen_paths: List[str] = []
    ingested, updated, skipped, unreadable = 0, 0, 0, 0
    docs = sorted([f for f in folder.iterdir()
                   if f.is_file() and f.suffix.lower() in (".md", ".pdf")
                   and not f.name.startswith("_")])
    for f in docs:
        body = _read_doc(f)
        if len(body) < 40:
            # Lege of onleesbare PDF (bv. gescand zonder tekstlaag) — meld het,
            # maar laat de rest van de sync doorlopen.
            if f.suffix.lower() == ".pdf":
                unreadable += 1
            continue
        # Voor PDF's is de bestandsnaam een betrouwbaarder titel dan de eerste
        # (vaak rommelige) tekstregel uit de extractie.
        title = f.stem if f.suffix.lower() == ".pdf" else _title_from(body, f.stem)
        path_key = str(f)
        seen_paths.append(path_key)
        h = _hash(body)
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id, content_hash FROM iris_knowledge WHERE source_path = ?",
                (path_key,),
            ).fetchone()
        if existing and existing["content_hash"] == h:
            skipped += 1
            continue
        distilled = await _distill(title, body)
        _upsert("vault", path_key, title, h, distilled)
        if existing:
            updated += 1
        else:
            ingested += 1

    # Verdwenen bestanden: deactiveren (niet hard verwijderen — history behouden).
    with get_conn() as conn:
        vault_rows = conn.execute(
            "SELECT id, source_path FROM iris_knowledge WHERE source = 'vault' AND active = 1"
        ).fetchall()
        removed = 0
        for r in vault_rows:
            if r["source_path"] and r["source_path"] not in seen_paths:
                conn.execute("UPDATE iris_knowledge SET active = 0, updated_at = ? WHERE id = ?",
                             (_now(), r["id"]))
                removed += 1

    report = {"ok": True, "ingested": ingested, "updated": updated,
              "skipped": skipped, "unreadable": unreadable,
              "deactivated": removed, "folder": str(folder)}
    logger.info("[iris-knowledge] sync: %s", report)
    return report


async def add_manual_note(title: str, text: str, scope: Optional[str] = None) -> Optional[str]:
    """Voeg kennis direct toe (geplakt in het dashboard, zonder vault-bestand).

    `scope`: forceer een project i.p.v. de LLM te laten gokken of dit voor
    één project of alle projecten geldt. Gebruikt door de onboarding-intake,
    waar de klant zelf al zegt voor wélk project dit is — daar is gissen fout.
    """
    body = (text or "").strip()
    if len(body) < 20:
        return None
    title = (title or _title_from(body, "Notitie")).strip()[:200]
    distilled = await _distill(title, body)
    if scope:
        distilled["scope"] = scope.strip()[:60]
    return _upsert("manual", "", title, _hash(body), distilled)


# ── Ophalen ─────────────────────────────────────────────────────────────────

def list_knowledge(include_inactive: bool = False) -> List[Dict[str, Any]]:
    q = ("SELECT id, source, source_path, title, summary, principles, tags, scope, "
         "active, updated_at FROM iris_knowledge")
    if not include_inactive:
        q += " WHERE active = 1"
    q += " ORDER BY updated_at DESC"
    out = []
    with get_conn() as conn:
        for r in conn.execute(q).fetchall():
            rec = dict(r)
            for k in ("principles", "tags"):
                try:
                    rec[k] = json.loads(rec.get(k) or "[]")
                except json.JSONDecodeError:
                    rec[k] = []
            out.append(rec)
    return out


def delete_knowledge(kid: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM iris_knowledge WHERE id = ?", (kid,))
        return cur.rowcount > 0


def active_principles(scope_project: Optional[str] = None,
                      tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Actieve kennisitems, optioneel gefilterd op project-scope en tags."""
    items = list_knowledge()
    want_tags = {t.lower() for t in tags} if tags else None
    out = []
    for it in items:
        if it["scope"] not in ("all", "") and scope_project and it["scope"].lower() != scope_project.lower():
            continue
        if want_tags and not (want_tags & {t.lower() for t in it["tags"]}):
            continue
        out.append(it)
    return out


def knowledge_prompt_block() -> str:
    """De kennisbank als promptsectie voor Iris' dagelijkse analyse.

    Elk actief item krijgt een titel + samenvatting (zodat Iris álle bronnen
    kent), plus zoveel principes als binnen het budget passen. Zo blijft de
    prompt begrensd zonder dat bronnen onzichtbaar wegvallen."""
    items = active_principles()
    if not items:
        return ""
    lines: List[str] = []
    budget = _MAX_PRINCIPLES_IN_PROMPT
    for it in items:
        scope = "" if it["scope"] in ("all", "") else f" [{it['scope']}]"
        tagtxt = f" ({', '.join(it['tags'])})" if it["tags"] else ""
        lines.append(f"### {it['title']}{scope}{tagtxt}")
        if it["summary"]:
            lines.append(it["summary"])
        # Verdeel het principe-budget eerlijk over de items zodat ook de laatste
        # bron nog een paar principes toont.
        per_item = max(2, budget // max(1, len(items)))
        for p in it["principles"][:per_item]:
            lines.append(f"- {p}")
    return "\n".join(lines)


def guidance_for_writing(project: Optional[str] = None) -> str:
    """Content-relevante principes voor de schrijf-agents (tags seo/content/geo/aeo).

    Dit is hoe Iris' kennis de agents die ze aanstuurt daadwerkelijk bereikt.
    """
    items = active_principles(scope_project=project,
                              tags=["seo", "content", "geo", "aeo", "ai-search", "merk"])
    if not items:
        return ""
    lines: List[str] = ["De volgende principes komen uit Iris' kennisbank — pas ze toe:"]
    count = 0
    for it in items:
        for p in it["principles"]:
            if count >= 16:
                break
            lines.append(f"- {p}")
            count += 1
    return "\n".join(lines) if count else ""
