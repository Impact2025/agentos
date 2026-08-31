"""Infinite Context Engine — API endpoint voor het dashboard.

GET /api/infinite-context/status
    → { configured, omi_configured, vault_path, note_count,
        today_session_count, recent_goals, recent_tasks,
        daily_log_preview }

GET /api/infinite-context/graph
    → { nodes: [...], links: [[i,j],...], groups: [...] }
    De volledige kennisgraaf van de vault (notities + wikilinks)
    voor de Memory Galaxy-visualisatie.

GET /api/infinite-context/note?path=...
    → { name, path, group, modified, content, outgoing, backlinks }

GET /api/infinite-context/search?q=...
    → { results: [{file, path, score, snippet}] }

Maakt de ICE zichtbaar in het dashboard zodat je ziet:
- Of de Obsidian vault + OMI actief zijn
- Wat er vandaag in het dagboek staat
- Welke taken/goals recent naar Obsidian zijn weggeschreven
- Hoe de Oneindige Loop draait
"""
import logging
import re
import time
from pathlib import Path
from datetime import date
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from ..shared.config import OBSIDIAN_VAULT_PATH
from .engine import InfiniteContextEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/infinite-context", tags=["infinite-context"])

_engine = InfiniteContextEngine(OBSIDIAN_VAULT_PATH)


def _get_folder_stats(vault: Path, subfolder: str) -> Dict[str, Any]:
    """Tel bestanden in een subfolder van de vault. Valt terug op de legacy
    'AgentOS/'-map als de nieuwe 'ImpactOS/'-map niet (nog) bestaat — zodat
    bestaande vault-content na de AgentOS->ImpactOS rename zichtbaar blijft."""
    folder = vault / subfolder
    legacy = vault / subfolder.replace("ImpactOS", "AgentOS", 1)
    if not folder.exists() and legacy.exists():
        folder = legacy
    if not folder.exists():
        return {"folder": subfolder, "count": 0, "recent_files": []}
    files = sorted(folder.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "folder": subfolder,
        "count": len(files),
        "recent_files": [
            {
                "name": f.stem,
                "relative_path": str(f.relative_to(vault)),
                "size": f.stat().st_size,
            }
            for f in files[:5]
        ],
    }


@router.get("/status")
def status() -> Dict[str, Any]:
    """Haal de Infinite Context Engine status/jongstleden activiteit op."""
    vault = _engine._vault_path

    base = {
        "configured": _engine.is_configured,
        "omi_configured": _engine.omi_configured,
        "vault_path": str(vault) if vault else "",
    }

    if not _engine.is_configured and not _engine.omi_configured:
        base["note"] = (
            "Infinite Context Engine is actief maar niet aangesloten op een bron. "
            "Stel OBSIDIAN_VAULT_PATH of OMI_API_KEY in .env in om de Oneindige Loop te starten."
        )
        return base

    # Tel bestanden in ImpactOS folders
    if vault:
        vault_p = Path(vault)
        base["note_count"] = sum(1 for _ in vault_p.rglob("*.md")) if vault_p.exists() else 0
        base["omi_configured"] = _engine.omi_configured

        # Subfolder statistieken
        base["folders"] = [
            _get_folder_stats(vault_p, "ImpactOS/Sessions"),
            _get_folder_stats(vault_p, "ImpactOS/Tasks"),
            _get_folder_stats(vault_p, "ImpactOS/Goals"),
        ]

    # Dagboek vandaag
    log_text = _engine._get_todays_log()
    if log_text:
        lines = [l.strip() for l in log_text.split("\n") if l.strip()]
        # Filter dubbele witregels en toon max 20 regels
        entries = []
        for l in lines:
            if l.startswith("#") or l.startswith("###"):
                entries.append(l)
            elif l and not l.startswith("-") and not l.startswith("---"):
                entries.append(l)
            elif l.startswith("- "):
                entries.append(l)
        base["daily_log_preview"] = "\n".join(entries[:30]) if entries else log_text[:1500]
        base["today_session_count"] = len([e for e in entries if e.startswith("###")])
    else:
        base["daily_log_preview"] = ""
        base["today_session_count"] = 0

    # OMI status
    if _engine.omi_configured:
        base["omi_env"] = bool(__import__("os").environ.get("OMI_API_KEY", ""))

    return base


# ═══════════════════════════════════════════════════════════════════
#  Memory Galaxy — kennisgraaf van de vault
# ═══════════════════════════════════════════════════════════════════

_SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)")
_MAX_READ_BYTES = 128 * 1024

_graph_cache: Dict[str, Any] = {"built_at": 0.0, "data": None}
_GRAPH_TTL_SECONDS = 120

# De Galaxy-fysica is O(n²) per simulatiestap (galaxySimStep in tabs-memory.js) —
# bij 6351 notities (aug 2026, ruim boven de ~500 waarop die aanname was gebaseerd)
# vroor het tabblad minutenlang vast op "Sterrenkaart laden...". De backend zelf
# is snel (~1s voor de hele vault); het knelpunt zit in de client-side simulatie.
# Cap de weergegeven sterren op de meest verbonden + meest recente notities i.p.v.
# de fysica O(n log n) te maken — de rest is toch niet leesbaar op één scherm.
_MAX_GRAPH_NODES = 800


def _vault_files(vault: Path) -> List[Path]:
    files = []
    for f in vault.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        files.append(f)
    return files


def _top_group(rel: Path) -> str:
    """Groepeer op bovenste map; losse notities in de root → 'Vault'."""
    return rel.parts[0] if len(rel.parts) > 1 else "Vault"


def _build_graph(vault: Path) -> Dict[str, Any]:
    files = _vault_files(vault)
    now = time.time()

    nodes: List[Dict[str, Any]] = []
    index_by_path: Dict[str, int] = {}
    index_by_stem: Dict[str, int] = {}

    for f in files:
        rel = f.relative_to(vault)
        try:
            stat = f.stat()
        except OSError:
            continue
        idx = len(nodes)
        rel_posix = rel.as_posix()
        nodes.append({
            "id": rel_posix,
            "name": f.stem,
            "group": _top_group(rel),
            "days": round((now - stat.st_mtime) / 86400, 1),
            "size": stat.st_size,
            "deg": 0,
        })
        index_by_path[rel_posix.lower()] = idx
        # Obsidian resolvet [[Naam]] op bestandsnaam; bij duplicaten wint de eerste
        index_by_stem.setdefault(f.stem.lower(), idx)

    links: List[List[int]] = []
    seen = set()
    for f in files:
        rel_posix = f.relative_to(vault).as_posix()
        src = index_by_path.get(rel_posix.lower())
        if src is None:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")[:_MAX_READ_BYTES]
        except OSError:
            continue
        for m in _WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            if not target:
                continue
            tgt = index_by_path.get(target.lower() + ".md")
            if tgt is None:
                tgt = index_by_stem.get(target.rsplit("/", 1)[-1].lower())
            if tgt is None or tgt == src:
                continue
            key = (min(src, tgt), max(src, tgt))
            if key in seen:
                continue
            seen.add(key)
            links.append([src, tgt])
            nodes[src]["deg"] += 1
            nodes[tgt]["deg"] += 1

    total_note_count = len(nodes)
    sampled = total_note_count > _MAX_GRAPH_NODES
    if sampled:
        # Meest verbonden eerst (interessant voor de kaart), bij gelijke graad de
        # meest recent bijgewerkte — dezelfde volgorde als de labeled-top-14 in de
        # frontend, alleen toegepast op de hele set i.p.v. alleen de labels.
        keep_order = sorted(range(total_note_count), key=lambda i: (-nodes[i]["deg"], nodes[i]["days"]))
        keep_idx = set(keep_order[:_MAX_GRAPH_NODES])
        remap: Dict[int, int] = {}
        new_nodes: List[Dict[str, Any]] = []
        for old_i in range(total_note_count):
            if old_i not in keep_idx:
                continue
            remap[old_i] = len(new_nodes)
            new_nodes.append(nodes[old_i])
        new_links = []
        for a, b in links:
            if a in keep_idx and b in keep_idx:
                new_links.append([remap[a], remap[b]])
        nodes, links = new_nodes, new_links

    # Groepen gesorteerd op omvang, zodat de frontend kleuren stabiel toewijst
    # (op de wérkelijk getoonde set — anders klopt de legenda niet met de sterren)
    group_counts: Dict[str, int] = {}
    for n in nodes:
        group_counts[n["group"]] = group_counts.get(n["group"], 0) + 1
    groups = sorted(group_counts, key=lambda g: -group_counts[g])

    return {
        "nodes": nodes,
        "links": links,
        "groups": groups,
        "note_count": len(nodes),
        "total_note_count": total_note_count,
        "sampled": sampled,
        "link_count": len(links),
        "built_at": now,
    }


@router.get("/graph")
def graph() -> Dict[str, Any]:
    """De kennisgraaf van de vault voor de Memory Galaxy."""
    if not _engine.is_configured:
        return {"nodes": [], "links": [], "groups": [], "note_count": 0, "link_count": 0}
    if _graph_cache["data"] and time.time() - _graph_cache["built_at"] < _GRAPH_TTL_SECONDS:
        return _graph_cache["data"]
    data = _build_graph(Path(str(_engine._vault_path)))
    _graph_cache["data"] = data
    _graph_cache["built_at"] = time.time()
    return data


@router.get("/note")
def note(path: str) -> Dict[str, Any]:
    """Inhoud + relaties van één notitie (voor het detailpaneel in de Galaxy)."""
    if not _engine.is_configured:
        raise HTTPException(404, "Vault niet geconfigureerd")
    vault = Path(str(_engine._vault_path)).resolve()
    target = (vault / path).resolve()
    if not target.is_relative_to(vault):
        raise HTTPException(400, "Pad buiten de vault")
    if not target.exists() or target.suffix != ".md":
        raise HTTPException(404, "Notitie niet gevonden")

    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        raise HTTPException(500, f"Kon notitie niet lezen: {e}")

    rel_posix = target.relative_to(vault).as_posix()
    outgoing = sorted({m.group(1).strip() for m in _WIKILINK_RE.finditer(text) if m.group(1).strip()})

    # Backlinks via de (gecachte) graaf
    g = graph()
    backlinks: List[str] = []
    idx = next((i for i, n in enumerate(g["nodes"]) if n["id"] == rel_posix), None)
    if idx is not None:
        for a, b in g["links"]:
            if a == idx:
                backlinks.append(g["nodes"][b]["name"])
            elif b == idx:
                backlinks.append(g["nodes"][a]["name"])

    stat = target.stat()
    return {
        "name": target.stem,
        "path": rel_posix,
        "group": _top_group(target.relative_to(vault)),
        "modified": date.fromtimestamp(stat.st_mtime).isoformat(),
        "size": stat.st_size,
        "content": text[:8000],
        "truncated": len(text) > 8000,
        "outgoing": outgoing[:30],
        "backlinks": sorted(set(backlinks))[:30],
    }


@router.get("/search")
def search(q: str, top_k: int = 15) -> Dict[str, Any]:
    """Doorzoek de vault (en OMI indien geconfigureerd)."""
    results: List[Dict[str, Any]] = []
    if _engine.is_configured and q.strip():
        obs = _engine._get_obsidian()
        if obs.is_configured:
            results = obs.search(q, top_k=top_k)
    omi_results: List[Dict[str, Any]] = []
    if _engine.omi_configured and q.strip():
        for m in _engine._omi.search_memories(q, limit=5):
            content = m.get("content", m.get("text", ""))
            if content:
                omi_results.append({"content": content[:300], "category": m.get("category", "")})
    return {"results": results, "omi": omi_results}
