"""Uitkomst-kaarten — één standaard voor 'wat heeft een agent gedaan'.

Elke agent-run (goal-taak, scheduler-job, strategist-actie, publish) sluit af
met een uitkomst: wat is er gedaan, waar staat het resultaat (artifact), en
wat moet de mens nog doen (next_step). Fouten krijgen status='error' zodat
het Actiecentrum ze als inbox-item toont.
"""
import uuid
import logging

from .database import get_conn

logger = logging.getLogger(__name__)


def log_outcome(
    project: str,
    action: str,
    detail: str,
    *,
    artifact: str = "",
    next_step: str = "",
    status: str = "ok",
) -> str:
    """Schrijf een uitkomst-kaart naar activity_log. Retourneert het id.

    artifact: URL of pad naar het concrete resultaat (leeg = geen artefact,
              wat voor een 'echte actie' een smell is).
    next_step: wat Vincent moet doen, in één zin. Leeg = niets.
    status: 'ok' | 'error'.
    """
    outcome_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (id, project, action, detail, artifact, next_step, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (outcome_id, project, action, detail, artifact, next_step, status),
        )
    return outcome_id


def log_llm_usage(
    *,
    backend: str,
    model: str,
    route: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    status: str = "ok",
    error: str = "",
) -> None:
    """Telemetrie voor één LLM-aanroep (alle routes: chat én autonome jobs).

    Schrijft naar `llm_usage` en slaat een WARN + activiteit als de geraamde
    dagelijkse kost de DAILY_TOKEN_BUDGET overschrijdt — zodat je ziet dat het
    hard gaat vóórdat de externe provider-quota ingestort is.
    """
    try:
        from .config import DAILY_TOKEN_BUDGET
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO llm_usage "
                "(backend, model, route, prompt_tokens, completion_tokens, total_tokens, status, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (backend, model, route, int(prompt_tokens), int(completion_tokens),
                 int(total_tokens), status, (error or "")[:500]),
            )
            today = conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS t FROM llm_usage "
                "WHERE date(created_at) = date('now')"
            ).fetchone()["t"]
        if DAILY_TOKEN_BUDGET and today and today >= DAILY_TOKEN_BUDGET:
            logger.warning(
                "[llm-usage] Dagverbruik %s tokens >= budget %s — controleer de kosten "
                "(zie tabel llm_usage, route='%s').",
                today, DAILY_TOKEN_BUDGET, route,
            )
    except Exception:
        # Telemetrie mag nooit een agent-run laten crashen.
        logger.debug("log_llm_usage mislukt", exc_info=True)


def llm_usage_summary(days: int = 7) -> dict:
    """Live verbruiksoverzicht voor het dashboard: waar gaan de credits heen.

    Groepeert vandaag per (route, model) zodat de grootverbruikers bovenaan
    staan, plus een dagreeks voor de trend. Kostenraming volgt de prijzen in
    .env (OPENMODEL_/ANTHROPIC_*_COST_PER_MTOK); 0 = alleen tokens tellen.
    """
    from .config import (
        DAILY_TOKEN_BUDGET,
        OPENMODEL_INPUT_COST_PER_MTOK, OPENMODEL_OUTPUT_COST_PER_MTOK,
        ANTHROPIC_INPUT_COST_PER_MTOK, ANTHROPIC_OUTPUT_COST_PER_MTOK,
    )

    def _cost(backend: str, prompt: int, completion: int) -> float:
        if backend == "anthropic":
            i, o = ANTHROPIC_INPUT_COST_PER_MTOK, ANTHROPIC_OUTPUT_COST_PER_MTOK
        else:
            i, o = OPENMODEL_INPUT_COST_PER_MTOK, OPENMODEL_OUTPUT_COST_PER_MTOK
        return round((prompt / 1e6) * i + (completion / 1e6) * o, 4)

    prices_configured = bool(
        OPENMODEL_INPUT_COST_PER_MTOK or OPENMODEL_OUTPUT_COST_PER_MTOK
        or ANTHROPIC_INPUT_COST_PER_MTOK or ANTHROPIC_OUTPUT_COST_PER_MTOK
    )
    days = max(1, min(int(days), 31))
    with get_conn() as conn:
        route_rows = conn.execute(
            "SELECT backend, model, route, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt, "
            "COALESCE(SUM(completion_tokens),0) AS completion, "
            "COALESCE(SUM(total_tokens),0) AS total, "
            "SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errors "
            "FROM llm_usage WHERE date(created_at) = date('now') "
            "GROUP BY backend, model, route ORDER BY total DESC"
        ).fetchall()
        day_rows = conn.execute(
            "SELECT date(created_at) AS d, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt, "
            "COALESCE(SUM(completion_tokens),0) AS completion, "
            "COALESCE(SUM(total_tokens),0) AS total, COUNT(*) AS calls "
            "FROM llm_usage WHERE date(created_at) >= date('now', ?) "
            "GROUP BY d",
            (f"-{days - 1} day",),
        ).fetchall()
        # Dagreeks in UTC-dagen — llm_usage.created_at is datetime('now') (UTC).
        dates = [r["d"] for r in conn.execute(
            "WITH RECURSIVE seq(n) AS (SELECT 0 UNION ALL SELECT n+1 FROM seq WHERE n < ?) "
            "SELECT date('now', '-' || n || ' day') AS d FROM seq ORDER BY d",
            (days - 1,),
        ).fetchall()]

    by_route = []
    for r in route_rows:
        by_route.append({
            "route": r["route"] or "(onbekend)",
            "backend": r["backend"], "model": r["model"],
            "calls": r["calls"], "prompt_tokens": r["prompt"],
            "completion_tokens": r["completion"], "total_tokens": r["total"],
            "errors": r["errors"] or 0,
            "cost": _cost(r["backend"], r["prompt"], r["completion"]) if prices_configured else None,
        })
    per_day = {r["d"]: r for r in day_rows}
    day_series = []
    for d in dates:
        r = per_day.get(d)
        day_series.append({
            "date": d,
            "total_tokens": r["total"] if r else 0,
            "calls": r["calls"] if r else 0,
            "cost": _cost("openmodel", r["prompt"], r["completion"]) if (r and prices_configured) else (0.0 if prices_configured else None),
        })
    today_total = sum(r["total_tokens"] for r in by_route)
    return {
        "budget": DAILY_TOKEN_BUDGET,
        "prices_configured": prices_configured,
        "today": {
            "total_tokens": today_total,
            "prompt_tokens": sum(r["prompt_tokens"] for r in by_route),
            "completion_tokens": sum(r["completion_tokens"] for r in by_route),
            "calls": sum(r["calls"] for r in by_route),
            "errors": sum(r["errors"] for r in by_route),
            "cost": round(sum(r["cost"] or 0 for r in by_route), 4) if prices_configured else None,
            "budget_pct": round(100.0 * today_total / DAILY_TOKEN_BUDGET, 1) if DAILY_TOKEN_BUDGET else None,
        },
        "by_route": by_route,
        "days": day_series,
    }


def daily_llm_tokens() -> int:
    """Totaal aantal tokens dat vandaag is gelogd (alle backends)."""
    try:
        with get_conn() as conn:
            return int(conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) FROM llm_usage "
                "WHERE date(created_at) = date('now')"
            ).fetchone()[0])
    except Exception:
        return 0


def note_llm_quota_exhausted(*, backend: str = "openmodel", model: str = "",
                             route: str = "") -> None:
    """Marker: de provider zei 403 quota-exceeded.

    Schrijft een 0-token rij met status='quota' in llm_usage. Zolang zo'n marker
    verser is dan LLM_QUOTA_BACKOFF_MINUTES behandelt llm_budget_exceeded() dat
    als 'budget op' — autonome jobs pauzeren dus vanzelf zodra de provider leeg
    is, zonder dat DAILY_TOKEN_BUDGET de échte (onbekende) providerlimiet hoeft
    te raden. De rem heft zichzelf op: na de backoff probeert de eerstvolgende
    run gewoon opnieuw.
    """
    log_llm_usage(backend=backend, model=model or "?", route=route,
                  status="quota", error="403 quota exceeded")


def llm_quota_backoff_active() -> bool:
    """True als de provider recent (binnen LLM_QUOTA_BACKOFF_MINUTES) 403 quota gaf."""
    try:
        from .config import LLM_QUOTA_BACKOFF_MINUTES
        if not LLM_QUOTA_BACKOFF_MINUTES:
            return False
        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE status = 'quota' "
                "AND created_at >= datetime('now', ?)",
                (f"-{int(LLM_QUOTA_BACKOFF_MINUTES)} minutes",),
            ).fetchone()[0]
        return n > 0
    except Exception:
        return False


class BudgetExceeded(RuntimeError):
    """De dagelijkse LLM-token-budget is overschreden.

    Autonome jobs (content-verbeteraar, biweekly content, radar-scan) gooien
    dit zodat ze niet nóg meer tokens verbranden als de quota al richting de
    limiet loopt. Een harde circuit-breaker bovenop de telemetrie — zodat een
    dag nooit meer de hele provider-quota leegzuigt (incident 2026-07-10).
    """


def llm_budget_exceeded() -> bool:
    """True zodra het geschatte dagverbruik de DAILY_TOKEN_BUDGET overschrijdt,
    óf de provider recent zelf 403 quota-exceeded zei (de zelf-uitlijnende rem)."""
    try:
        if llm_quota_backoff_active():
            return True
        from .config import DAILY_TOKEN_BUDGET
        if not DAILY_TOKEN_BUDGET:
            return False
        return daily_llm_tokens() >= DAILY_TOKEN_BUDGET
    except Exception:
        return False


def require_llm_budget(route: str = "") -> None:
    """Gooi BudgetExceeded als de dagbudget op is; log één activiteit per dag.

    Autonome jobs roepen dit aan bij de start van een run. De mens ziet in het
    Actiecentrum dat de automatisering gepauzeerd is i.p.v. een lege quota.
    De kaart benoemt de échte reden: interne budgetgrens of een provider-403.
    """
    if not llm_budget_exceeded():
        return
    from .config import DAILY_TOKEN_BUDGET, LLM_QUOTA_BACKOFF_MINUTES
    used = daily_llm_tokens()
    quota_hit = llm_quota_backoff_active()
    if quota_hit:
        detail = (
            f"OpenModel-quota op (403 van de provider) — autonome LLM-runs pauzeren "
            f"{LLM_QUOTA_BACKOFF_MINUTES} min en proberen daarna vanzelf opnieuw."
        )
        next_step = ("Niets — de agents proberen het vanzelf opnieuw. Duurt het uren: "
                     "verhoog de quota op openmodel.ai.")
    else:
        detail = (
            f"Dagelijkse LLM-token-budget ({DAILY_TOKEN_BUDGET:,}) bereikt "
            f"(~{used:,} tokens gebruikt). Autonome content/radar-runs gepauzeerd "
            f"tot de budget-counter reset (middernacht) of de quota is aangevuld."
        )
        next_step = "Verhoog DAILY_TOKEN_BUDGET in .env, of vul de OpenModel/Anthropic-quota aan."
    logger.warning("[llm-budget] LLM-runs gepauzeerd voor route '%s': %s", route, detail)
    try:
        with get_conn() as conn:
            seen = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action = 'llm-budget-op' "
                "AND date(created_at) = date('now')"
            ).fetchone()[0]
        # Eén kaart per dag: elke geblokkeerde job hetzelfde laten melden
        # verandert niets aan wat Vincent moet doen, het begraaft alleen de rest.
        if not seen:
            log_outcome("?", "llm-budget-op", detail, next_step=next_step, status="error")
    except Exception:
        logger.debug("require_llm_budget: log_outcome mislukt", exc_info=True)
    raise BudgetExceeded(f"LLM-runs gepauzeerd — route '{route}': {detail}")
