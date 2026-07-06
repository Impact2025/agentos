from .base import Tool, ToolResult
from ..shared.config import OBSIDIAN_VAULT_PATH
from ..domains.chat.obsidian import ObsidianService


class ObsidianSearchTool(Tool):
    name = "obsidian_search"
    description = (
        "Zoek relevante notities in de persoonlijke Obsidian vault — de eigen, eerder "
        "opgeslagen kennis van de gebruiker (projecten, ideeen, aantekeningen, historische data). "
        "Gebruik deze tool NOOIT voor actuele informatie, nieuws of feiten van buitenaf: daarvoor is "
        "web_search. Alleen gebruiken voor wat de gebruiker zelf in de vault heeft vastgelegd."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Zoekterm of vraag"},
            "top_k": {"type": "integer", "description": "Aantal resultaten (standaard 5)", "default": 5},
        },
        "required": ["query"],
    }

    async def run(self, query: str, top_k: int = 5) -> ToolResult:
        obs = ObsidianService(OBSIDIAN_VAULT_PATH)
        if not obs.is_configured:
            return ToolResult(self.name, "Obsidian vault niet geconfigureerd (OBSIDIAN_VAULT_PATH ontbreekt in .env).", error=True)

        results = obs.search(query, top_k=top_k)
        if not results:
            return ToolResult(self.name, f"Geen relevante notities gevonden voor '{query}'.")

        parts = [f"Gevonden {len(results)} notitie(s) voor '{query}':\n"]
        for r in results:
            parts.append(f"### {r['file']}  ({r['path']})\n{r['snippet']}\n")
        return ToolResult(self.name, "\n".join(parts))
