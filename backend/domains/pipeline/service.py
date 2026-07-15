"""
Julian — Triage & Orchestrator Agent.

Accepteert een gebruikersprompt en produceert een lineaire keten van subtaken
in JSON, direct bruikbaar voor de conveyor loop en database.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.config import OPENROUTER_API_KEY, HERMES_MODEL
from ...shared.database import get_conn
from ...shared.agent_runner import run_agent

# Toegestane statussen in de conveyor state-machine.
TRIAGE_STATUSES = ("todo", "ready", "running", "done", "awaiting_approval")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_system_prompt() -> str:
    return """
Je bent Julian, de Triage & Orchestrator Agent.
Je taak is om complexe opdrachten op te knippen in een assembly line van subtaken.

BELANGRIJK: Genereer ALTIJD concreet, uitvoerbare taken. Geen adviezen of algemene instructies.

# STRIKTE REGELS VOOR B2B PROSPECTING
Wanneer de gebruiker het heeft over het vinden van B2B-klanten:
1. keyword_agent = genereer een lijst met daadwerkelijk bestaande bedrijfsnamen + KVK-nummer + plaats
2. outline_agent = bouw een outreachstructuur per bedrijf
3. writer_agent = schrijf korte, warme outreachteksten per bedrijf
4. link_agent = voeg alleen zakelijke links toe (geen persoonsgegevens!)

# STRIKTE JSON-REGELS
- Reageer UITSLUITEND met JSON, geen markdown
- Geen inleidende tekst, geen uitleg na het JSON-object
- Gebruik alleen toegestane waarden voor `assigned_to`
- Eerste taak krijgt status "ready", alle anderen "todo"
- Voeg voor elke opdracht precies 4 subtaken toe in deze volgorde:
  1. keyword_agent (concrete zoektermen/bedrijven/data)
  2. outline_agent (structuur op basis van stap 1)
  3. writer_agent (tekst op basis van stap 2)
  4. link_agent (optimalisatie + links)

# VORBEELD GOEDE JSON
{
  "project_name": "Concrete projectnaam",
  "subtasks": [
    {
      "title": "Zoek 10 specifieke notariskantoren in Rotterdam met KVK-nummer",
      "description": "Gebruik browserautomatisering om op kvk.nl te zoeken naar 'notaris' in 'Rotterdam'. Schrijf de resultaten naar 01-keyword_agent.md met bedrijfsnaam, KVK-nummer, plaats en website.",
      "assigned_to": "keyword_agent",
      "status": "ready",
      "depends_on_index": null
    },
    {
      "title": "Bouw outreachstructuur per notaris",
      "description": "Lees 01-keyword_agent.md. Maak voor elk bedrijf een korte outreach-opzet met aanleiding, waarde en vraag. Schrijf naar 02-outline_agent.md.",
      "assigned_to": "outline_agent",
      "status": "todo",
      "depends_on_index": 0
    },
    {
      "title": "Schrijf warme outreachtekst per bedrijf",
      "description": "Lees 02-outline_agent.md. Schrijf per bedrijf een korte (max 150 woorden) toegankelijke outreachtekst. Schrijf naar 03-writer_agent.md.",
      "assigned_to": "writer_agent",
      "status": "todo",
      "depends_on_index": 1
    },
    {
      "title": "Controleer op persoonsgegevens en voeg zakelijke links toe",
      "description": "Lees 03-writer_agent.md. Verwijder eventuele persoonsgegevens. Voeg alleen zakelijke links toe (kvk.nl, eigen website). Schrijf finale versie naar 04-link_agent.md.",
      "assigned_to": "link_agent",
      "status": "awaiting_approval",
      "depends_on_index": 2
    }
  ]
}
"""


def _build_user_prompt(user_prompt: str) -> str:
    return f"Gebruiker wil: {user_prompt}\nGenereer de subtakenketen in JSON."


def _extract_json(raw: str) -> str:
    """Haal het JSON-object uit een LLM-antwoord, ook als het in ```-fences of
    inleidende tekst verpakt zit. Pakt de eerste '{' t/m de laatste '}'."""
    s = raw.strip()
    if s.startswith("```"):
        # Verwijder ```json ... ``` fences en hou de inhoud over.
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s.strip()


async def create_triage_plan(user_prompt: str) -> Dict[str, Any]:
    system_prompt = _build_system_prompt()
    user_message = _build_user_prompt(user_prompt)

    chunks: List[str] = []
    async for event in run_agent(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        agent="hermes",
    ):
        text = event.get("text") or ""
        chunks.append(text)

    raw = "".join(chunks).strip()
    try:
        plan = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Julian leverde geen geldig JSON. Output: {raw[:500]!r}"
        ) from exc

    _validate_plan(plan)
    return plan


def _validate_plan(plan: Dict[str, Any]) -> None:
    if "project_name" not in plan or "subtasks" not in plan:
        raise ValueError("Ontbrekende velden: project_name en subtasks zijn verplicht.")

    allowed_agents = {"keyword_agent", "outline_agent", "writer_agent", "link_agent"}
    allowed_statuses = {"ready", "todo", "running", "done", "awaiting_approval"}

    for index, task in enumerate(plan.get("subtasks", [])):
        if task.get("assigned_to") not in allowed_agents:
            task["assigned_to"] = "hermes"
        status = task.get("status")
        if status not in allowed_statuses:
            raise ValueError(
                f"Ongeldige status '{status}' voor taak op index {index}."
            )
        if index == 0:
            task["status"] = "ready"
        else:
            if task.get("status") == "ready":
                task["status"] = "todo"
        if task.get("depends_on_index") is None and index > 0:
            task["depends_on_index"] = index - 1


# ── DB-/state-machine-laag ─────────────────────────────────────────────
# Deze functies vormen de lijm tussen Julians plan, de tasks-tabel en de
# conveyor loop. tasks.py en conveyor_loop.py importeren ze rechtstreeks.

def _task_row(conn, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


# De vier specialisten die Julian toewijst, als echte agent-profielen met een
# eigen expert-system-prompt. Zo draait elke pijplijnstap met een ander "brein"
# i.p.v. allemaal hetzelfde generieke default-profiel.
_DEFAULT_MODEL = "openrouter/meta-llama/llama-3.1-8b-instruct"
SPECIALISTS: Dict[str, Dict[str, str]] = {
    "keyword_agent": {
        "name": "Keyword & Prospect Researcher",
        "system_prompt": (
            "Je bent een SEO-zoekwoorden- en B2B-prospectonderzoeker. Je levert UITSLUITEND "
            "concrete, verifieerbare data: echte bedrijfsnamen, plaatsen, websites en (waar relevant) "
            "KVK-nummers, of concrete zoekwoorden met zoekintentie. Geen algemeenheden of adviezen. "
            "Output is een nette Markdown-tabel of -lijst, direct bruikbaar door de volgende agent. "
            "Verzin geen data; markeer onzekerheid expliciet."
        ),
    },
    "outline_agent": {
        "name": "Content Structuur Architect",
        "system_prompt": (
            "Je bent een content-architect. Op basis van de aangeleverde research bouw je een heldere, "
            "logische structuur: koppen, secties en per onderdeel een korte instructie wat erin moet. "
            "Voor outreach maak je per prospect een opzet (aanleiding, waarde, call-to-action). "
            "Output is gestructureerde Markdown, geen lopende tekst, geen meta-uitleg."
        ),
    },
    "writer_agent": {
        "name": "Copywriter",
        "system_prompt": (
            "Je bent een Nederlandstalige copywriter. Je schrijft op basis van de aangeleverde structuur "
            "warme, heldere, bondige teksten (max ~150 woorden per outreachbericht). Toegankelijke toon, "
            "actief taalgebruik, geen jargon, geen clichés. Lever direct bruikbare Markdown zonder uitleg "
            "vooraf of achteraf."
        ),
    },
    "link_agent": {
        "name": "Link Builder & Compliance",
        "system_prompt": (
            "Je bent eindredacteur en compliance-controleur. Je controleert de tekst op feitelijke en "
            "AVG-risico's, verwijdert persoonsgegevens, en voegt alleen zakelijke, relevante links toe "
            "(eigen website, kvk.nl). Lever de finale, gepubliceerde Markdown-versie."
        ),
    },
}


def _ensure_specialist_profiles(conn) -> Dict[str, int]:
    """Zorg dat de vier specialist-profielen bestaan (idempotent) en geef een
    map van sub-agent-slug → profiel-id terug."""
    mapping: Dict[str, int] = {}
    now = _now()
    for slug, spec in SPECIALISTS.items():
        row = conn.execute(
            "SELECT id FROM agent_profiles WHERE name = ?", (spec["name"],)
        ).fetchone()
        if row:
            mapping[slug] = row["id"]
            continue
        cur = conn.execute(
            "INSERT INTO agent_profiles (name, model, system_prompt, created_at) "
            "VALUES (?, ?, ?, ?)",
            (spec["name"], _DEFAULT_MODEL, spec["system_prompt"], now),
        )
        mapping[slug] = cur.lastrowid
    return mapping


def create_triage(user_prompt: str, workspace_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Draai Julian, persisteer de subtakenketen in de tasks-tabel en geef ze terug.

    Wordt aangeroepen vanuit een synchrone FastAPI-route (draait in een
    threadpool zonder actieve event loop), daarom asyncio.run().
    """
    plan = asyncio.run(create_triage_plan(user_prompt))

    project_slug = _slugify(plan.get("project_name") or "project")
    base_dir = (workspace_path or project_slug).strip("/")

    now = _now()
    created_ids: List[str] = []
    with get_conn() as conn:
        profile_map = _ensure_specialist_profiles(conn)
        for index, sub in enumerate(plan.get("subtasks", [])):
            task_id = str(uuid.uuid4())
            assigned_to = sub.get("assigned_to") or "hermes"
            out_file = f"{base_dir}/{index + 1:02d}-{assigned_to}.md"
            conn.execute(
                """INSERT INTO tasks
                   (id, title, description, status, agent, assigned_agent_id,
                    position, workspace_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    sub.get("title") or f"Subtaak {index + 1}",
                    sub.get("description") or "",
                    sub.get("status") or ("ready" if index == 0 else "todo"),
                    assigned_to,
                    profile_map.get(assigned_to),
                    index,
                    out_file,
                    now,
                    now,
                ),
            )
            created_ids.append(task_id)

        rows = [dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()) for tid in created_ids]
    return rows


def get_next_ready_task() -> Optional[Dict[str, Any]]:
    """De eerstvolgende taak met status 'ready' (op volgorde van de keten)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status = 'ready' "
            "ORDER BY position ASC, created_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_ready_tasks(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Alle taken met status 'ready' in één query.

    Belangrijk: in deze state-machine wordt een taak pas 'ready' zodra zijn
    keten-voorganger 'done' is (zie set_task_status). Elke 'ready' taak is
    daarmee *dependency-safe* en mag parallel met de andere 'ready' taken
    worden afgevuurd — ze horen altijd tot verschillende ketens (per keten
    staat hooguit één taak tegelijk op 'ready'). Dit is de "segmentatie" van
    het conveyor: onafhankelijke taken tegelijk, afhankelijke wachten tot hun
    voorganger klaar is.

    `limit` begrenst het aantal taken per batch (concurrency-cap) zodat de
    LLM-gateway niet onder 50 tegelijke calls bezwijkt.
    """
    with get_conn() as conn:
        sql = (
            "SELECT * FROM tasks WHERE status = 'ready' "
            "ORDER BY position ASC, created_at ASC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_agent_profile(profile_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Haal een agent-profiel op (model + system_prompt) voor de conveyor."""
    if not profile_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    return dict(row) if row else None


def get_previous_result(task: Dict[str, Any]) -> Optional[str]:
    """Geef het resultaat van de vorige afgeronde stap in dezelfde keten.

    Stappen delen een workspace-basismap (bijv. 'project-x/01-keyword_agent.md').
    De vorige stap = de taak met lagere positie binnen dezelfde basismap die al
    een resultaat heeft. Zo krijgt elke agent de output van zijn voorganger als input.
    """
    wp = (task.get("workspace_path") or "").strip()
    if "/" not in wp:
        return None
    base = wp.rsplit("/", 1)[0]
    pos = task.get("position") or 0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result FROM tasks "
            "WHERE workspace_path LIKE ? AND position < ? "
            "AND result IS NOT NULL AND result != '' "
            "ORDER BY position DESC LIMIT 1",
            (base + "/%", pos),
        ).fetchone()
    return row["result"] if row and row["result"] else None


# Kolommen die de conveyor naast de status mag bijwerken.
_RESULT_FIELDS = ("result", "error", "started_at", "finished_at", "duration_ms")


def set_task_status(task_id: str, status: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Zet de status van een taak (+ optioneel resultaat/telemetrie) en schuif de keten door.

    Extra kwargs uit `_RESULT_FIELDS` (result, error, started_at, finished_at,
    duration_ms) worden in dezelfde transactie weggeschreven, zodat agent-output
    nooit verloren gaat. Wanneer een taak 'done' wordt, promoveert de
    eerstvolgende 'todo'-taak (op ketenvolgorde) naar 'ready'.
    """
    if status not in TRIAGE_STATUSES:
        raise ValueError(f"Ongeldige status '{status}'. Toegestaan: {TRIAGE_STATUSES}")

    extra = {k: v for k, v in fields.items() if k in _RESULT_FIELDS and v is not None}
    set_clause = "status = ?, updated_at = ?"
    values: List[Any] = [status, _now()]
    for col, val in extra.items():
        set_clause += f", {col} = ?"
        values.append(val)
    values.append(task_id)

    with get_conn() as conn:
        if not _task_row(conn, task_id):
            return None
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        if status == "done":
            nxt = conn.execute(
                "SELECT id FROM tasks WHERE status = 'todo' "
                "ORDER BY position ASC, created_at ASC LIMIT 1"
            ).fetchone()
            if nxt:
                conn.execute(
                    "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                    (_now(), nxt["id"]),
                )
        return _task_row(conn, task_id)


def _slugify(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "project"
