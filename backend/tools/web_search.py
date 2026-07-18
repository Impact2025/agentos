import asyncio
from concurrent.futures import ThreadPoolExecutor
from .base import Tool, ToolResult

_executor = ThreadPoolExecutor(max_workers=4)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Zoek actuele informatie op het open web: nieuws, feiten, documentatie of informatie "
        "die niet in de eigen Obsidian vault staat. Gebruik dit altijd voor recente of "
        "tijdsgevoelige vragen. NIET gebruiken voor financiele koersen/marktdata (gebruik "
        "get_market_data) of voor de eigen opgeslagen notities (gebruik obsidian_search)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Zoekopdracht"},
            "max_results": {"type": "integer", "description": "Aantal resultaten (standaard 5)", "default": 5},
        },
        "required": ["query"],
    }

    async def run(self, query: str, max_results: int = 5) -> ToolResult:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, self._search, query, max_results)
        return result

    def _search(self, query: str, max_results: int) -> ToolResult:
        from ..shared import websearch
        try:
            results = websearch.search(query, max_results=max_results)
            hits = [
                f"**{r['title']}**\n{r['snippet']}\nBron: {r['url']}"
                for r in results
            ]
            if not hits:
                return ToolResult(self.name, f"Geen zoekresultaten voor '{query}'.")
            return ToolResult(self.name, "\n\n---\n\n".join(hits))
        except Exception as e:
            return ToolResult(self.name, f"Zoekfout: {e}", error=True)
