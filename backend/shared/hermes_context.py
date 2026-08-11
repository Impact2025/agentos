"""
Hermes-skills ↔ Agent OS context-brug.

Doel: Agent OS stuurt Hermes aan als een generieke LLM (default system prompt
"You are Hermes, a helpful AI assistant."). Daardoor mist de autonome
content/schrijf-pipeline de scherpe projectcontext die wél in Vincent's
Hermes-skills (C:/Users/v_mun/AppData/Local/hermes/skills) en zijn Obsidian
SCHRIJF-DNA-vaultnotes zit. Resultaat: platte boilerplate.

Deze module leest die twee bronnen en bouwt één compacte context-string die
overal aan de system prompt kan worden geplakt. Defensief: elke bron kan
ontbreken zonder crash; lege bron = lege string.

Opt-in via .env:
    AGENTOS_USE_HERMES_SKILLS=true
    HERMES_SKILLS_DIR=C:/Users/v_mun/AppData/Local/hermes/skills
Als de toggle uit staat (default) geeft build_hermes_context() altijd "" terug,
zodat bestaande callers nooit van behaviour veranderen.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

# ----------------------------------------------------------------------------- config
def _use_hermes_skills() -> bool:
    return os.getenv("AGENTOS_USE_HERMES_SKILLS", "false").lower() in ("1", "true", "yes", "on")


def _allowed_projects() -> set:
    """Comma-gescheiden project-allowlist. Leeg = geldt voor alle projecten
    (wanneer de master-toggle aan staat). Niet-leeg = alleen deze projecten
    krijgen Hermes-context. Projectnaam-vergelijking is case-insensitive."""
    raw = os.getenv("AGENTOS_HERMES_SKILLS_PROJECTS", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _skills_dir() -> Optional[Path]:
    raw = os.getenv("HERMES_SKILLS_DIR", "")
    if raw:
        p = Path(raw)
        if p.exists():
            return p
    # Fallback op de standaard-locatie voor deze gebruiker.
    fallback = Path.home() / "AppData" / "Local" / "hermes" / "skills"
    if fallback.exists():
        return fallback
    return None


def _vault_dir() -> Optional[Path]:
    raw = os.getenv("OBSIDIAN_VAULT_PATH", "")
    if raw:
        p = Path(raw)
        if p.exists():
            return p
    return None


# ------------------------------------------------------------------------- skills-lezer
def _read_skill_frontmatter_name(text: str) -> Optional[str]:
    """Pak de 'name:' uit een SKILL.md frontmatter, of None."""
    m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _load_skill_body(path: Path, max_chars: int = 1800) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    # Skip de YAML-frontmatter, hou de markdown-body.
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3:].strip()
    return text[:max_chars]


def _hermes_skill_context(project_name: Optional[str], max_total: int = 2600) -> str:
    """Lees relevante Hermes-skills: de 'agentos'-categorie altijd, plus elke
    skill waarvan de naam/projecthint matcht met het project."""
    sdir = _skills_dir()
    if not sdir:
        return ""

    parts: List[str] = []
    total = 0

    # 1) Agentos-specifieke skills altijd meenemen (operationale kennis).
    agentos_cat = sdir / "agentos"
    agentos_files: List[Path] = []
    if agentos_cat.exists():
        for p in sorted(agentos_cat.rglob("SKILL.md")):
            agentos_files.append(p)

    # 2) Project-specifieke match: doorzoek alle categorieën op de projectnaam.
    project_hits: List[Path] = []
    if project_name:
        needle = project_name.lower()
        for cat in sorted(sdir.iterdir()):
            if not cat.is_dir():
                continue
            for p in cat.rglob("SKILL.md"):
                txt = p.read_text(encoding="utf-8", errors="ignore").lower()
                if needle in txt or needle in str(p).lower():
                    project_hits.append(p)

    for p in agentos_files + project_hits:
        body = _load_skill_body(p)
        if not body:
            continue
        title = _read_skill_frontmatter_name(
            p.read_text(encoding="utf-8", errors="ignore")
        ) or p.parent.name
        block = f"### Hermes-skill: {title}\n{body}"
        if total + len(block) > max_total:
            break
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts)


# --------------------------------------------------------------------- vault SCHRIJF-DNA
def _vault_schrijf_dna(project_name: Optional[str], max_total: int = 2400) -> str:
    """Lees de SCHRIJF-DNA-<Project>.md note uit de vault (10_Projects/<p>/)."""
    vdir = _vault_dir()
    if not vdir or not project_name:
        return ""

    pdir = vdir / "10_Projects" / project_name
    if not pdir.exists():
        return ""

    # Match op SCHRIJF-DNA-<Project>.md (case-insensitive, spatie/ongeveer).
    needle = project_name.lower().replace(" ", "")
    best: Optional[Path] = None
    for md in pdir.glob("SCHRIJF-DNA*.md"):
        stem = md.stem.lower().replace(" ", "")
        if needle in stem or "schrijf-dna" in stem:
            best = md
            break
    if best is None:
        # Fallback: elke SCHRIJF-DNA-*.md in de projectmap.
        hits = list(pdir.glob("SCHRIJF-DNA*.md"))
        if hits:
            best = hits[0]
    if best is None:
        return ""

    try:
        text = best.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    # Knip frontmatter weg.
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3:].strip()

    block = f"### Schrijf-DNA ({best.stem})\n{text[:max_total]}"
    return block


# --------------------------------------------------------------------------- publiek
def build_hermes_context(project_name: Optional[str] = None, max_chars: int = 5000) -> str:
    """Geef een kant-en-klare context-string voor de Hermes system prompt.

    Retourneert altijd een string; leeg als de feature uit staat of geen
    bronnen beschikbaar zijn. Nooit een exception.
    """
    if not _use_hermes_skills():
        return ""

    # Project-allowlist: als gevuld, alleen deze projecten krijgen context.
    allowed = _allowed_projects()
    if allowed and project_name:
        if project_name.lower() not in allowed:
            return ""
    elif allowed and not project_name:
        # Geen projectnaam opgegeven maar wel een allowlist: niets injecteren,
        # anders zouden niet-toegestane pipelines alsnog context krijgen.
        return ""

    try:
        skill_ctx = _hermes_skill_context(project_name)
        dna_ctx = _vault_schrijf_dna(project_name)

        parts = [p for p in (skill_ctx, dna_ctx) if p]
        if not parts:
            return ""

        joined = "\n\n".join(parts)
        if len(joined) > max_chars:
            joined = joined[:max_chars]
        return (
            "## Hermes Agent OS-context (skills + schrijf-DNA)\n\n" + joined
        )
    except Exception:
        # Defensief: context mag nooit een crash veroorzaken.
        return ""
