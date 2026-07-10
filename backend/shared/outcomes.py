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


class BudgetExceeded(RuntimeError):
    """De dagelijkse LLM-token-budget is overschreden.

    Autonome jobs (content-verbeteraar, biweekly content, radar-scan) gooien
    dit zodat ze niet nóg meer tokens verbranden als de quota al richting de
    limiet loopt. Een harde circuit-breaker bovenop de telemetrie — zodat een
    dag nooit meer de hele provider-quota leegzuigt (incident 2026-07-10).
    """


def llm_budget_exceeded() -> bool:
    """True zodra het geschatte dagverbruik de DAILY_TOKEN_BUDGET overschrijdt."""
    try:
        from .config import DAILY_TOKEN_BUDGET
        if not DAILY_TOKEN_BUDGET:
            return False
        return daily_llm_tokens() >= DAILY_TOKEN_BUDGET
    except Exception:
        return False


def require_llm_budget(route: str = "") -> None:
    """Gooi BudgetExceeded als de dagbudget op is; log één activiteit bij de grens.

    Autonome jobs roepen dit aan bij de start van een run. De mens ziet in het
    Actiecentrum dat de automatisering gepauzeerd is i.p.v. een lege quota.
    """
    if not llm_budget_exceeded():
        return
    from .config import DAILY_TOKEN_BUDGET
    used = daily_llm_tokens()
    logger.warning(
        "[llm-budget] Dagbudget %s tokens bereikt (gebruikt ~%s) — autonome LLM-run "
        "geblokkeerd voor route '%s'. Verhoog DAILY_TOKEN_BUDGET of vul de quota aan.",
        DAILY_TOKEN_BUDGET, used, route,
    )
    try:
        log_outcome(
            "?", "llm-budget-op",
            f"Dagelijkse LLM-token-budget ({DAILY_TOKEN_BUDGET:,}) bereikt "
            f"(~{used:,} tokens gebruikt). Autonome content/radar-runs gepauzeerd "
            f"tot de budget-counter reset (middernacht) of de quota is aangevuld.",
            next_step="Verhoog DAILY_TOKEN_BUDGET in .env, of vul de OpenModel/Anthropic-quota aan.",
            status="error",
        )
    except Exception:
        logger.debug("require_llm_budget: log_outcome mislukt", exc_info=True)
    raise BudgetExceeded(
        f"LLM-dagbudget bereikt ({used:,}/{DAILY_TOKEN_BUDGET:,} tokens) — "
        f"route '{route}' gepauzeerd."
    )
