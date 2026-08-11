"""Agenda-tool voor de Iris-chat-agent.

Tot nu toe kon Iris de agenda alleen LEZEN (get_today_summary in de context)
en antwoordde ze in de chat "ik heb geen tool om afspraken aan te maken". De
backend kón het echter al lang: nl_command.parse_command parseert de NL-zin,
check_conflict toetst tegen free/busy, en bridge.actions._cmd_calendar_add legt
het als calendar_proposal (status=pending_review) neer. Wat ontbrak was de
kóppeling naar de agentic tool-loop (backend/tools/TOOLS) — deze tool sluit dat
gat.

Bewust ontwerp (zie iris_remote UI "Publiceren, mailen en boeken blijft jouw
tik"): we boeken NIET direct in Google Agenda. De tool zet een voorstel klaar —
inclusief reistijd- en conflict-analyse — dat Vincent in het Actiecentrum met
één tik goedkeurt. Zo krijgt Iris de mogelijkheid om afspraken/terugkerende
blokken aan te maken, zonder de review-gate te omzeilen die dubbele boekingen
en per-ongeluk-geplande afspraken tegenhoudt.

De tool accepteert de vrije NL-zin die de gebruiker toch al intypt, bv.:
  "blok de komende 6 weken op maandag van 08.30 tot 10.00 voor Focustijd"
  "dinsdag 18 augustus om 12.15 naar de tandarts"
  "online meeting met Thijs Lenting op 19 augustus 10.00"
"""
from .base import Tool, ToolResult


class CalendarCreateTool(Tool):
    name = "calendar_create"
    description = (
        "Maak een agenda-afspraak of terugkerend blok aan uit een vrije "
        "Nederlandse zin. Gebruik dit zodra de gebruiker vraagt om iets in de "
        "agenda te zetten, te blokkeren, te reserveren of te plannen (bv. "
        "'blok elke maandag 08.30-10.00 voor Focustijd' of 'dinsdag 18 augustus "
        "12.15 tandarts'). De afspraak wordt met reistijd- en conflict-analyse "
        "als voorstel klaargezet en verschijnt in het Actiecentrum ter "
        "goedkeuring — één tik van de gebruiker en hij staat in Google Agenda. "
        "Je hoeft dus NIET meer te zeggen dat je geen agenda-tool hebt; roep "
        "deze tool aan met de volledige zin van de gebruiker."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "De volledige natuurlijke opdracht van de gebruiker, met "
                    "dag/datum, tijd(vak) en onderwerp. Geef de zin zo compleet "
                    "mogelijk door — de parser haalt zelf datum, tijd, duur, "
                    "locatie, deelnemers en herhaling eruit. Voorbeeld: 'blok de "
                    "komende 6 weken op maandag van 08.30 tot 10.00 voor "
                    "Focustijd'."
                ),
            },
        },
        "required": ["text"],
    }

    async def run(self, text: str = "") -> ToolResult:
        text = (text or "").strip()
        if not text:
            return ToolResult(
                self.name,
                "Geen opdracht meegegeven. Geef de hele zin door, bv. "
                "'dinsdag 18 augustus 12.15 tandarts'.",
                error=True,
            )
        try:
            # Hergebruik de bestaande, geteste review-gate-logica: parse →
            # conflict-check → calendar_proposal (pending_review). Eén bron van
            # waarheid, geen tweede parser die uit de pas gaat lopen.
            from ..domains.bridge.actions import _cmd_calendar_add
            ok, message = await _cmd_calendar_add({"text": text})
            return ToolResult(self.name, message, error=not ok)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                self.name, f"Kon het agenda-voorstel niet aanmaken: {e}", error=True
            )
