---
name: Pootgelukkig
description: "Pootgelukkig — adoptieplatform voor asieldieren, koppelt asielen aan adoptanten"
version: 1.0.0
tags: [adoptie, asiel, dieren, vrijwilligers, herplaatsing]
---

# Pootgelukkig

## Merkidentiteit
- **Website:** https://pootgelukkig.nl
- **Toon:** Warm, hoopvol, toegankelijk — overal waar adoptie centraal staat
- **Doelgroep:** Mensen die een dier willen adopteren, asielmedewerkers, vrijwilligers
- **Kernboodschap:** "Elk dier verdient een gelukkig thuis."

## Workflow
- CRM pipeline: Excel → genereer_ts.py → nl-asielen.ts → db:import-asielen → crm:asielen
- Telegram bot: @pootgelukkig_bot
- Content: adoptieverhalen, asielinformatie, dierverzorgingstips
