"""Infinite Context Engine — de brug tussen AI Agents en Obsidian.

Three-part loop (The Loop):
  1. Read:  Vraagt Obsidian-context op vóór elke agent-run
  2. Act:   Voegt context in system prompt, agent voert taak uit
  3. Write: Logt resultaat terug naar Obsidian (dagelijks log + taak-specifiek)

Over tijd groeit de Obsidian vault als een zelflerend geheugen:
  - OMI/notities vullen de input-kant
  - Agent-sessies vullen de output-kant
  - Elke nieuwe run leest alle historie → agent wordt elke dag slimmer
"""
from .engine import InfiniteContextEngine

__all__ = ["InfiniteContextEngine"]
