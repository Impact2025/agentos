"""
Delegate Tool — hiermee delegeert de Lead Agent (chat) parallelle achtergrond-
taken aan gespecialiseerde subagents.

Cruciaal: `run()` keert ONMIDDELLIJK terug. Het start de workers via
spawn_delegation (die een achtergrond-task aanmaakt) en geeft direct de controle
terug aan de Lead Agent / UI. De resultaten stromen later asynchroon binnen via
de event-bus (/api/delegate/stream).
"""
from .base import Tool, ToolResult


class DelegateTool(Tool):
    name = "delegate"
    description = (
        "Delegeer werk aan een team parallelle subagents (workers) die in de "
        "achtergrond draaien. Gebruik dit voor grotere opdrachten die uiteenvallen "
        "in ONAFHANKELIJKE deeltaken — bijv. een complete SEO/content-funnel "
        "(keyword research + meerdere blogposts + interne linkstrategie). "
        "Splits de opdracht zelf op in concrete, op zichzelf staande workers. "
        "Deze tool blokkeert NIET: hij start de workers en keert direct terug; "
        "elk worker-resultaat verschijnt zodra het klaar is als zelfstandig bericht. "
        "Gebruik dit NIET voor één enkele, kleine vraag — beantwoord die zelf."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "description": "De overkoepelende opdracht in één zin, bv. 'SEO-funnel voor keyword X'.",
            },
            "workers": {
                "type": "array",
                "description": "De onafhankelijke deeltaken. Geef er 2-6. Elke worker draait parallel in zijn eigen context.",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "description": "Korte rolnaam, bv. 'Keyword Researcher' of 'Blogpost: titel'."},
                        "goal": {"type": "string", "description": "Concreet, self-contained doel + verwacht eindproduct voor deze worker."},
                        "profile": {"type": "string", "description": "Optioneel: naam van een bestaand agent-profiel dat als 'brein' fungeert."},
                    },
                    "required": ["role", "goal"],
                },
            },
            "cta": {
                "type": "string",
                "description": "Optioneel: de verplichte call-to-action / conversiehook die elke worker in de tekst moet verweven.",
            },
        },
        "required": ["objective", "workers"],
    }

    async def run(self, objective: str, workers: list, cta: str = "", session_id: str = "") -> ToolResult:
        # Lazy import: voorkomt een circulaire import (tools → delegate_service →
        # agent_service → tools) tijdens het opstarten.
        from ..domains.delegate import service as delegate_service
        try:
            result = delegate_service.spawn_delegation(
                objective=objective,
                workers=workers,
                session_id=session_id or None,
                cta=cta or None,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(self.name, f"Kon delegatie niet starten: {e}", error=True)

        roles = ", ".join(result["roles"])
        return ToolResult(
            self.name,
            f"✅ {result['worker_count']} workers gestart (batch `{result['delegation_id']}`): {roles}. "
            f"Ze draaien parallel in de achtergrond; elk resultaat verschijnt zodra het klaar is. "
            f"Je hoeft niet te wachten — vat voor de gebruiker kort samen wat er nu loopt.",
        )


class DelegationStatusTool(Tool):
    name = "delegation_status"
    description = "Bekijk de status en (deel)resultaten van een eerder gestarte delegatie-batch."
    input_schema = {
        "type": "object",
        "properties": {
            "delegation_id": {"type": "string", "description": "De batch-id die delegate teruggaf."},
        },
        "required": ["delegation_id"],
    }

    async def run(self, delegation_id: str) -> ToolResult:
        from ..domains.delegate import service as delegate_service
        d = delegate_service.get_delegation(delegation_id)
        if not d:
            return ToolResult(self.name, f"Geen delegatie gevonden met id '{delegation_id}'.", error=True)
        lines = [f"**Delegatie {delegation_id}** — status: {d['status']} ({d['worker_count']} workers)"]
        for w in d["workers"]:
            mark = {"done": "✅", "error": "❌", "running": "⏳", "queued": "•"}.get(w["status"], "•")
            lines.append(f"{mark} [{w['status']}] {w['role']} — {w['goal'][:80]}")
        return ToolResult(self.name, "\n".join(lines))
