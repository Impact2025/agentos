# Hermes × ImpactOS — Agent Workforce System

Geïnspireerd op Allie K. Miller's "AI Agent Workforce" (Greg Isenberg podcast),
toegesneden op Vincent's portfolio (ImpactOS, Hermes, IctusGo, OpenModel).

Drie componenten, één samenhangend systeem:

## 0. Unified CLI (workforce.py)
Eén commando voor alles — je hoeft de losse scripts niet te kennen:
- `python3 workforce.py add "tekst" [--project IctusGo] [--tag strategy]`
- `python3 workforce.py digest [--days 14] [--project IctusGo]`
- `python3 workforce.py toby`            (watchdog + self-heal + dashboard)
- `python3 workforce.py status`          (korte gezondheids-samenvatting)
- `python3 workforce.py bootstrap --interview x.json --out workforce.yaml`

## 1. AI Diary (context-engine) — per-project
`ai_diary.py` — vers context, **per project afgebakend**.
- `add "tekst" [--project IctusGo] [--tag focus|strategy|meeting|idea|blocker|win]`
  → schrijft naar `10_Projects/_ai_diary/YYYY-MM-DD.md` met `#<project>`-tag.
- `digest [--days 14] [--project IctusGo]`
  - zonder `--project`: volledige `AI_DIARY_DIGEST.md` met per-project secties.
  - met `--project`: aparte `AI_DIARY_DIGEST_<project>.md` (volledige digest blijft
    intact) → een workforce laadt alleen z'n eigen context (minder ruis).
- `last [--days 7] [--project IctusGo]`

Cron: dagelijks `digest` (07:00) zodat de digest vers blijft.

## 2. Toby (Workforce Watchdog)
`toby.py` — proactief frictie-signaal vóóraf + **self-healing**.
Probes: ImpactOS :1250 healthcheck, Hermes-gateway :8899, Ollama :11434,
AI Diary-verversing. Schrijft `Toby-LATEST.md` + `Toby-Report-<date>.md` en
print een samenvatting (cron-levering).

Severity: RED (ImpactOS/Ollama down), AMBER (budget>85%, stalled goals,
diary>2d oud, of gateway was down maar automatisch herstart), GREEN.

**Self-heal:** als Hermes-gateway :8899 down is, start Toby automatisch de
Omniroute-supervisor (`D:/apps/llm-proxy/supervisor.py`) — die bewaakt zowel
gateway :8899 als ImpactOS :1250 en herstart bij crash. Geen menselijke actie nodig.

**Outputs (naast de .md-rapporten):**
- `Toby-LATEST.json` — machine-leesbaar (severity, findings, gateway/ollama live,
  agentos_bugs.stalled_goals, diary_age_days). Voeding voor externe dashboards/tools.
- `dashboard.html` — self-contained, refresht elke 120s op `Toby-LATEST.json`.
  Open: `file:///D:/APPS/Hermes%20Brein/Hermes%20Breind/10_Projects/_ai_diary/dashboard.html`

Cron: dagelijks 08:00 (no_agent) + optioneel `workforce.py status` voor een snelle check.

## 3. Bootstrap (interview → workforce-spec)
`bootstrap.py` — Allie's "laat een agent je interviewen"-patroon.
- Interview (zie skill `agent-workforce-bootstrap`) → `antwoorden.json`
- `bootstrap.py --interview antwoorden.json --out workforce-<project>.yaml`
  → op maat gemaakte orchestrator + directors + guardrails.

## Architectuur-keuze
- Stdlib-only → geen pip, draait in elke cron-context.
- Vault als single source of truth (AI Diary + Toby-rapporten).
- ImpactOS /api/healthcheck als gezaghebbende statusbron voor Toby.
- Escalatie-gates: externe writes (GitHub/e-mail/Stripe/publish) altijd via
  mens/go-no-go in de orchestrator-laag — dit is de enige échte veiligheidseis
  die Miller's "proactiviteit" Nietzsche-achtig kan laten ontsporen.
