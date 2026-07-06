"""
Obsidian Vault scanner met keyword-based relevance ranking.

Aanpak:
- Scant recursief alle .md bestanden
- Berekent per bestand een relevantiescore via term-frequency (TF) over zoektermen
- Geeft de beste chunks terug als context voor de AI
"""
import re
import math
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional


_STOP_WORDS = {
    "de", "het", "een", "en", "van", "in", "is", "dat", "op", "te",
    "the", "a", "an", "and", "or", "in", "is", "it", "of", "to", "for",
    "with", "that", "this", "was", "are", "be", "as", "at",
}


class ObsidianService:
    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path) if vault_path else None

    @property
    def is_configured(self) -> bool:
        return self.vault_path is not None and self.vault_path.exists()

    def list_files(self) -> List[Dict]:
        if not self.is_configured:
            return []
        files = []
        for p in sorted(self.vault_path.rglob("*.md")):
            try:
                stat = p.stat()
                files.append({
                    "name": p.stem,
                    "path": str(p.relative_to(self.vault_path)),
                    "size": stat.st_size,
                })
            except OSError:
                continue
        return files

    def total_file_count(self) -> int:
        if not self.is_configured:
            return 0
        return sum(1 for _ in self.vault_path.rglob("*.md"))

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        if not self.is_configured or not query.strip():
            return []

        terms = self._tokenize(query)
        if not terms:
            return []

        scored: List[Dict] = []

        for p in self.vault_path.rglob("*.md"):
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            score = self._score(content, terms)
            if score > 0:
                snippet = self._best_snippet(content, terms)
                scored.append({
                    "file": p.stem,
                    "path": str(p.relative_to(self.vault_path)),
                    "score": score,
                    "snippet": snippet,
                    "full_content": content,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def build_context(self, query: str, max_chars: int = 3000) -> str:
        """Geeft een kant-en-klare context-string terug voor de system prompt."""
        results = self.search(query, top_k=4)
        if not results:
            return ""
        parts = []
        total = 0
        for r in results:
            block = f"### {r['file']}\n{r['snippet']}"
            if total + len(block) > max_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ helpers

    def write_note(self, project_name: str, slug: str, title: str, content_html: str,
                   metadata: Optional[Dict] = None) -> Optional[str]:
        """
        Sla een artikel op als markdown note in de Obsidian vault.

        Structuur: 10_Projects/{project_name}/artikels/{slug}.md
        content_html wordt ongewijzigd opgeslagen — Obsidian renderet HTML prima.
        """
        if not self.is_configured:
            return None

        note_dir = self.vault_path / "10_Projects" / project_name / "artikels"
        note_dir.mkdir(parents=True, exist_ok=True)

        # YAML frontmatter
        meta_lines = ["---"]
        meta_lines.append(f'title: "{title}"')
        meta_lines.append(f"slug: {slug}")
        meta_lines.append(f"created: {datetime.now().strftime('%Y-%m-%d')}")
        if metadata:
            for key, val in metadata.items():
                if isinstance(val, str):
                    meta_lines.append(f'{key}: "{val}"')
                else:
                    meta_lines.append(f"{key}: {val}")
        meta_lines.append("---")
        meta_lines.append("")
        meta_lines.append(content_html.strip())
        meta_lines.append("")

        note_path = note_dir / f"{slug}.md"
        note_path.write_text("\n".join(meta_lines), encoding="utf-8")
        return str(note_path)

    def _tokenize(self, text: str) -> List[str]:
        words = re.findall(r"[a-zA-ZÀ-ɏ]{2,}", text.lower())
        return [w for w in words if w not in _STOP_WORDS]

    def _score(self, content: str, terms: List[str]) -> float:
        lower = content.lower()
        # TF: aantal keer dat elke term voorkomt, genormaliseerd op doclengte
        word_count = max(len(lower.split()), 1)
        score = 0.0
        for term in terms:
            tf = lower.count(term)
            if tf:
                score += (1 + math.log(tf)) / math.log(1 + word_count)
        return score

    def _best_snippet(self, content: str, terms: List[str], window: int = 350) -> str:
        lower = content.lower()
        best_pos = 0
        best_hits = 0

        # Schuifvenster van ~80 chars om beste ankerpositie te vinden
        positions = []
        for term in terms:
            idx = 0
            while True:
                found = lower.find(term, idx)
                if found == -1:
                    break
                positions.append(found)
                idx = found + 1

        for pos in positions:
            win_start = max(0, pos - 40)
            win_end = min(len(lower), pos + 80)
            hits = sum(lower[win_start:win_end].count(t) for t in terms)
            if hits > best_hits:
                best_hits = hits
                best_pos = pos

        start = max(0, best_pos - 100)
        end = min(len(content), start + window)
        raw = content[start:end].strip()

        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(content) else ""
        return prefix + raw + suffix
