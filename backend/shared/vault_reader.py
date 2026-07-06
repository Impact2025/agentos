"""
Vault Reader — lees Obsidian vault notities voor projectcontext.

Hiermee kan elk onderdeel (blog suggesties, SEO-pipeline, advice)
de juiste Obsidian notes laden zonder de vault structuur te hoeven kennen.

Voorbeelden:
  vr = VaultReader()
  brand = vr.get_project_core("WeAreImpact")  # leest [000] WEAREIMPACT_CORE/
  areas = vr.get_area("Bewaardvoorjou")       # leest 20_Areas/
  actions = vr.get_pending_actions("WeAreImpact")  # actiepunten uit Areas
"""

from pathlib import Path
from typing import Dict, List, Optional
import os
import re

from .config import OBSIDIAN_VAULT_PATH


class VaultReader:
    """Lees context uit de Obsidian vault voor project-specifieke prompts."""

    def __init__(self, vault_path: Optional[str] = None):
        self.vault = Path(vault_path or OBSIDIAN_VAULT_PATH or "")
        self._cache: Dict[str, str] = {}

    @property
    def is_configured(self) -> bool:
        return self.vault.exists() and self.vault.is_dir()

    def _read(self, path: Path) -> str:
        """Read file with caching."""
        key = str(path)
        if key not in self._cache:
            if path.exists():
                self._cache[key] = path.read_text("utf-8", errors="ignore")
            else:
                self._cache[key] = ""
        return self._cache[key]

    def _find_file(self, directory: str, filename: str) -> Optional[Path]:
        """Find a file in a vault subfolder (case-insensitive)."""
        if not self.is_configured:
            return None
        folder = self.vault / directory
        if not folder.exists():
            return None
        for f in folder.iterdir():
            if f.is_file() and f.name.lower() == filename.lower():
                return f
        # Stretch: grab the first .md file as fallback
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() == ".md":
                return f
        return None

    def _read_core_folder(self, project_name: str) -> Dict[str, str]:
        """Lees alle bestanden uit de [NNN] PROJECTNAME_CORE/ map."""
        if not self.is_configured:
            return {}
        results = {}
        for folder in self.vault.iterdir():
            if not folder.is_dir():
                continue
            name_clean = folder.name.split("]", 1)[-1].strip().lower()
            if project_name.lower() in name_clean and "core" in name_clean:
                for f in folder.iterdir():
                    if f.is_file() and f.suffix.lower() == ".md":
                        stem = f.stem.replace("_", " ").strip()
                        results[stem] = self._read(f)
        return results

    def get_core_context(self, project_name: str) -> str:
        """Geef alle core context voor een project als één string.

        - Context wordt op TTL-cache geplaatst (5 minuten)
        - Token-optimalisatie via samenvatten
        """
        notes = self._read_core_folder(project_name)
        if not notes:
            return ""

        # TTL cache check
        cache_key = f"core_{project_name}"
        now = __import__('time').time()
        if cache_key in self._cache and isinstance(self._cache.get(cache_key), tuple):
            timestamp, cached = self._cache[cache_key]
            if now - timestamp < 300:  # 5 minuten TTL
                return cached

        from .token_optimizer import deduplicate_context, truncate_to_token_budget
        notes = deduplicate_context(notes)

        parts = []
        for title, content in notes.items():
            # Strip frontmatter
            body = content
            if body.startswith("---"):
                idx = body.find("---", 3)
                if idx > 0:
                    body = body[idx + 3:].strip()
            parts.append(f"## {title}\n{body}")
        result = "\n\n".join(parts)

        # Cache resultaat
        self._cache[cache_key] = (now, result)
        return result

    def get_area_content(self, area_name: str) -> str:
        """Lees 20_Areas/{area_name}.md voor actiepunten en verantwoordelijkheden."""
        f = self._find_file("20_Areas", f"{area_name}.md")
        if not f:
            return ""
        return self._read(f)

    def get_project_dashboard(self, project_name: str) -> str:
        """Lees 10_Projects/{project_name}.md voor actiepunten."""
        f = self._find_file("10_Projects", f"{project_name}.md")
        if not f:
            return ""
        return self._read(f)

    def get_pending_actions(self, project_name: str) -> List[str]:
        """Haal alle onafgehandelde actiepunten ([] checkboxes) uit Areas en Projects."""
        actions = []
        # Scan project dashboard
        dash = self.get_project_dashboard(project_name)
        if dash:
            for line in dash.split("\n"):
                if re.match(r"-\s*\[\s*\]", line):
                    actions.append(line.strip("- [] ").strip())
        # Scan area note
        area = self.get_area_content(project_name)
        if area:
            for line in area.split("\n"):
                if re.match(r"-\s*\[\s*\]", line):
                    actions.append(line.strip("- [] ").strip())
        return actions

    def get_recent_analytics(self) -> str:
        """Lees het meest recente Analytics rapport voor inzicht."""
        if not self.is_configured:
            return ""
        analytics_dir = self.vault / "Analytics"
        if not analytics_dir.exists():
            return ""
        md_files = sorted(analytics_dir.glob("*.md"), reverse=True)
        if not md_files:
            return ""
        latest = self._read(md_files[0])
        # Strip frontmatter, return samenvatting
        if latest.startswith("---"):
            idx = latest.find("---", 3)
            if idx > 0:
                latest = latest[idx + 3:].strip()
        return latest[:2000]

    def get_project_specific_note(self, project_name: str, note_name: str) -> str:
        """Zoek een specifieke notitie in een project-submap (bv. Pootgelukkig/SEO/)."""
        if not self.is_configured:
            return ""
        # Scan project directories for the note
        for folder in self.vault.iterdir():
            if not folder.is_dir():
                continue
            if project_name.lower() in folder.name.lower():
                for f in folder.rglob("*.md"):
                    if note_name.lower() in f.stem.lower():
                        return self._read(f)
        return ""
