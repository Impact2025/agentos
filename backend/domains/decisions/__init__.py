"""Besluiten — openstaande keuzes die om een besluit vragen, per project.

Geen aparte workflow-engine: een besluit is `open` totdat Vincent hem afrondt
(dan wordt de keuze en de reden vastgelegd, status → `besloten`). Dat maakt
het tegelijk een actief beslismoment (wat vraagt nu om een besluit) én een
logboek (wat is er ooit besloten en waarom) — twee vragen, één tabel, want
een besluit dat je nam is geen ander soort record dan een besluit dat nog
moet, alleen een andere status.
"""
