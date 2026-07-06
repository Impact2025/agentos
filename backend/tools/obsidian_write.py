from .base import Tool, ToolResult
from ..shared.config import OBSIDIAN_VAULT_PATH
from ..domains.chat.obsidian import ObsidianService


class ObsidianWriteTool(Tool):
    name = "obsidian_write"
    description = (
        "Schrijf een nieuwe notitie naar de Obsidian vault, of voeg inhoud toe aan een bestaande notitie. "
        "Gebruik dit om bevindingen, samenvatingen of gegenereerde content op te slaan."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Bestandsnaam zonder .md extensie. Submappen zijn toegestaan, bijv. 'Agent-Output/rapport-2026'",
            },
            "content": {"type": "string", "description": "Inhoud in Markdown formaat"},
            "append": {
                "type": "boolean",
                "description": "true = toevoegen aan bestaand bestand, false = overschrijven (standaard false)",
                "default": False,
            },
        },
        "required": ["filename", "content"],
    }

    async def run(self, filename: str, content: str, append: bool = False) -> ToolResult:
        obs = ObsidianService(OBSIDIAN_VAULT_PATH)
        if not obs.is_configured:
            return ToolResult(self.name, "Obsidian vault niet geconfigureerd.", error=True)

        if not filename.endswith(".md"):
            filename += ".md"

        path = obs.vault_path / filename
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append and path.exists():
                existing = path.read_text(encoding="utf-8")
                path.write_text(existing + "\n\n" + content, encoding="utf-8")
                return ToolResult(self.name, f"Toegevoegd aan '{filename}'.")
            else:
                path.write_text(content, encoding="utf-8")
                return ToolResult(self.name, f"Notitie '{filename}' opgeslagen in vault.")
        except OSError as e:
            return ToolResult(self.name, f"Schrijffout: {e}", error=True)
