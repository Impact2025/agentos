"""Holding-brede context voor De Sparringpartner.

25 aug 2026: dit domein begon als een tweerichtingsbrug naar mijn-ondernemers-
os (Next.js/Neon). Bleek fout — Vincents dagelijkse ritueel zit al in
ImpactOS' eigen `backend/domains/rituals`, dus De Sparringpartner (`backend/
domains/coach`) leeft nu native en heeft geen bridge meer nodig voor zijn
kernfunctie. Wat overblijft: `router.py` ontsluit `/api/coach-context/holding`
— read-only, token-gated — voor het geval een extern systeem ooit weer wil
meelezen. `context.py:build_holding_context()` wordt daarnaast in-process
hergebruikt door `coach/service.py`.
"""
