"""Gedeelde 'is deze pijler voor dit project vandaag al opgepakt'-toets.

Iris' briefing (`iris/actions.py:content_run`/`seo_refresh`) en Agent Control se
pijler-dispatcher (`agentctl/suggest.py`) zijn twee onafhankelijke besliswegen
naar hetzelfde werk: allebei lezen ze de pijlerscores uit `iris/metrics.py` en
allebei mogen ze zelfstandig een contentmotor- of SEO-run voor een project
starten. Vóór 22 aug 2026 hadden ze elk hun eigen dedup-tabel (Iris:
`activity_log`, actie `iris_actie`; Agent Control: `agentctl_deploys`) en wisten
ze niets van elkaar.

Concreet gat: `iris/metrics.py:_content_pillar` telt alleen `status='published'`
mee, nooit `pending_review`. Schrijft Iris om 06:45 een artikel, dan blijft de
content-score van dat project laag — het concept staat pas ter goedkeuring, is
nog niet live. Om 07:00 draait de scheduler-job `iris_auto_deploy`
(`agentctl/suggest.py:auto_deploy_daily`) en ziet exact diezelfde pijler nog
steeds als de zwakste, en start een tweede, volledige Gauntlet-run voor
hetzelfde project — het duurste pad in het systeem (zie CLAUDE.md
"Eén weg naar de Gauntlet", 15 aug 2026, voor de kosten van zo'n dubbele weg).

Deze module is de ene plek die "vandaag al gebeurd" beantwoordt voor de twee
pijlers waar dit kan gebeuren (content, seo) — beide mechanismen roepen 'm aan
vóórdat ze zelf werk starten, in plaats van elk hun eigen, onvolledige
administratie te raadplegen.
"""
from __future__ import annotations

from ...shared.database import get_conn

# Pijler -> het detail-voorvoegsel dat Iris' eigen acties in `activity_log`
# gebruiken (zie iris/actions.py). Alleen pijlers die Iris zelf ook kan
# starten staan hier — 'uitvoering'/'hygiene' bestaan uitsluitend bij Agent
# Control en hebben dus geen Iris-kant om tegen te toetsen.
_IRIS_DETAIL_PREFIX = {
    "content": "Contentmotor gestart",
    "seo": "SEO-refresh gestart",
}


def pillar_handled_today(project: str, pillar: str) -> bool:
    """True zodra óf Iris' briefing óf Agent Control deze pijler voor dit
    project vandaag al heeft aangepakt (gestart, bezig, of klaar zonder
    effect) — ongeacht welk van de twee het was."""
    with get_conn() as conn:
        prefix = _IRIS_DETAIL_PREFIX.get(pillar)
        if prefix:
            row = conn.execute(
                "SELECT 1 FROM activity_log WHERE action = 'iris_actie' AND project = ? "
                "AND detail LIKE ? AND date(created_at) = date('now', 'localtime') LIMIT 1",
                (project, prefix + "%"),
            ).fetchone()
            if row:
                return True
        row = conn.execute(
            "SELECT 1 FROM agentctl_deploys WHERE project = ? AND pillar = ? AND "
            "(status = 'running' OR (status IN ('staged', 'no_effect') "
            "AND date(created_at) = date('now'))) LIMIT 1",
            (project, pillar),
        ).fetchone()
        return row is not None
