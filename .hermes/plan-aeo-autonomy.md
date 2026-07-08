# Plan: AEO-autonomie maximaliseren (wereldklasse)

Doel: de agent doet alles zelf tot aan de publicatie-gate. De mens klikt
alleen nog "publiceer" in de Wachtrij. De Wachtrij-gate (CLAUDE.md regel 5)
blijft heilig — er wordt NOOIT automatisch gepubliceerd.

## Huidige keten (handmatig)
scan (4u) → signaal 'new' → [MENS klikt AEO-aanval] → 3 conveyor-taken
→ [MENS wacht op conveyor] → [MENS klikt queue-listicle] → Wachtrij
→ [MENS klikt publiceer] → live

## Nieuwe keten (autonoom tot gate)
scan (4u, of "Scan nu") → top-signaal (score≥AUTO_AEO_MIN_SCORE)
→ auto-AEO-aanval → 3 conveyor-taken
→ Conveyor voert ze zelfstandig uit (eigen kwaliteitscheck, status
  'done' i.p.v. wachten op mens) → listicle afgerond
→ auto-stage naar Wachtrij (pending_review / needs_work)
→ [MENS klikt publiceer] → live

## Wijzigingen
1. config.py: AEO_AUTO_ATTACK (0/1), AEO_AUTO_MIN_SCORE (default 75),
   AEO_AUTO_MAX_PER_SCAN (default 3), HERMES_LOCAL_FALLBACK (0/1).
   + .env.example entries.
2. radar/service.py: na run_scan (en in scan_the_skies) →
   `_auto_aeo_top_signals()` : pak nieuwe signalen met score≥min,
   nog niet 'converted', max N per scan, roep aeo_attack() aan.
   Idempotent: alleen 'new'-signalen.
3. conveyor.py: na _execute_task succes → eigen kwaliteitscheck
   (lengte/minimale structuur). Bij goed: status 'done' (klaar voor
   downstream). Bij slecht: 'needs_work' + error-log. Verwijder de
   'awaiting_approval'-tussenstop zodat de keten doorrolt zonder
   menselijke klik. Taak-faal → 'todo' (bestaand).
4. radar/service.py: queue_listicle() wordt autonoom aangeroepen zodra
   de listicle-taak 'done' is (in conveyor of via een lightweight
   poller). Nieuw: `_auto_stage_ready_listicles()`.
5. agent_runner.py: bij ontbrekende/gefaalde backend → als
   HERMES_LOCAL_FALLBACK=1, produceer een deterministische
   template-vuller (duidelijk gemarkeerd CONCEPT) zodat de pijplijn
   niet stilvalt. Taak krijgt status 'needs_work' (gate weigert <80).
6. scheduler.py: geen nieuwe job nodig — auto-AEO zit in scan_the_skies,
   auto-stage in de conveyor zelf.

## Veiligheid
- Geen enkele wijziging raakt approve_and_publish().
- Auto-AEO alleen op 'new' signalen met hoge score (geen spam).
- Fallback-content is altijd <80 score → nooit automatisch gepubliceerd.

## Test
tests/test_aeo_autonomy.py:
- auto-AEO kiest juiste top-signalen, max N, idempotent.
- conveyor zet afgeronde taak op 'done'.
- queue_listicle-stage zet job in pending_review.
- fallback bij geen backend levert 'needs_work'.
