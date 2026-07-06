import json
from .base import Tool, ToolResult
from ..domains.analytics.ga_service import fetch_weekly_data, is_configured


class GoogleAnalyticsTool(Tool):
    name = "get_analytics"
    description = (
        "Haalt Google Analytics 4 data op voor de website. "
        "Geeft sessies, gebruikers, paginaweergaven, verkeersbronnen, top pagina's, "
        "apparaten en landen terug voor de afgelopen N dagen. "
        "Gebruik dit altijd als de gebruiker vraagt naar websitestatistieken, bezoekersaantallen of GA-data."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Aantal dagen terug om data op te halen (standaard 7, max 90)",
                "default": 7,
            }
        },
        "required": [],
    }

    async def run(self, days: int = 7, **kwargs) -> ToolResult:
        if not is_configured():
            return ToolResult(
                tool_name=self.name,
                output="Google Analytics is niet geconfigureerd. Stel GA4_PROPERTY_ID en GA_SERVICE_ACCOUNT_PATH in via .env.",
                error=True,
            )
        try:
            days = max(1, min(days, 90))
            data = fetch_weekly_data(days=days)
            return ToolResult(tool_name=self.name, output=json.dumps(data, ensure_ascii=False))
        except Exception as e:
            return ToolResult(tool_name=self.name, output=f"Fout bij ophalen GA data: {e}", error=True)
