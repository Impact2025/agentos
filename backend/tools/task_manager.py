import uuid
from datetime import datetime, timezone
from .base import Tool, ToolResult
from ..shared.database import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreateTaskTool(Tool):
    name = "create_task"
    description = "Maak een nieuwe taak aan in het Kanban-bord van Impact OS."
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Korte taakomschrijving"},
            "description": {"type": "string", "description": "Optionele details of context", "default": ""},
            "status": {
                "type": "string",
                "enum": ["backlog", "in_progress", "done"],
                "description": "Kanban-kolom (standaard: backlog)",
                "default": "backlog",
            },
        },
        "required": ["title"],
    }

    async def run(self, title: str, description: str = "", status: str = "backlog") -> ToolResult:
        task_id = str(uuid.uuid4())
        now = _now()
        try:
            with get_conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO tasks (id, title, description, status, agent, position, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, title, description, status, "hermes", count, now, now),
                )
            return ToolResult(self.name, f"Taak aangemaakt: '{title}' in kolom '{status}'.")
        except Exception as e:
            return ToolResult(self.name, f"Kon taak niet aanmaken: {e}", error=True)


class ListTasksTool(Tool):
    name = "list_tasks"
    description = "Geef een overzicht van taken in het Kanban-bord, optioneel gefilterd op status."
    input_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["backlog", "in_progress", "done", "all"],
                "description": "Filter op kolom (standaard: all)",
                "default": "all",
            },
        },
    }

    async def run(self, status: str = "all") -> ToolResult:
        try:
            with get_conn() as conn:
                if status == "all":
                    rows = conn.execute(
                        "SELECT title, description, status FROM tasks ORDER BY status, position"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT title, description, status FROM tasks WHERE status = ? ORDER BY position",
                        (status,),
                    ).fetchall()

            if not rows:
                return ToolResult(self.name, "Geen taken gevonden.")

            lines = [f"**Taken ({len(rows)}):**"]
            for r in rows:
                line = f"- [{r['status']}] {r['title']}"
                if r["description"]:
                    line += f"  —  {r['description']}"
                lines.append(line)
            return ToolResult(self.name, "\n".join(lines))
        except Exception as e:
            return ToolResult(self.name, f"Kon taken niet ophalen: {e}", error=True)
