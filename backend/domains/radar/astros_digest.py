"""Astros Daily Digest — het ochtendrapport van je concurrent-radar.

Elke ochtend (scheduler-cron) loopt dit over de verse signalen + de
momentum-tabel en schrijft één samenvattend rapport naar je Obsidian-SSOT
(10_Projects/_trends/_astros-digest/YYYY-MM-DD.md). Dat is dezelfde vault
die je chat-agent leest, dus "waar moet ik deze week content over maken?"
krijgt automatisch de verse concurrentie-intelligentie als context.

Het rapport bevat:
  * Top momentum-signalen (wat explodeert er op dit moment)
  * Nieuwe high-score signalen van de laatste 24u
  * Per signaal: de GEBOUWDE merk-invalshoek (via build_hermes_context /
    SCHRIJF-DNA), zodat de hoek niet generiek maar jouw-stem is
  * Een "signal → content" actielijst (1-klik equivalent in tekst)

Defensief: faalt nooit hard. Zonder vault schrijft het naar een log-string.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ...shared.config import OBSIDIAN_VAULT_PATH
from ...shared.database import get_conn
from . import momentum

log = __import__("logging").getLogger(__name__)

DIGEST_DIR = "10_Projects/_trends/_astros-digest"
_LOOKBACK_HOURS = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brand_context(project: str) -> str:
    """Haal merk/SCHRIJF-DNA-context op voor de hoek-generatie (opt-in safe)."""
    try:
        from ...shared.hermes_context import build_hermes_context
        ctx = build_hermes_context(project, max_chars=1800)
        return ctx or ""
    except Exception:  # noqa: BLE001
        return ""


def build_digest(project: Optional[str] = None) -> Dict:
    """Verzamel de data voor het dagrapport. Retourneert een dict met de
    gestructureerde secties; schrijft NIET (dat doet write_daily_digest)."""
    now = datetime.now(timezone.utc)
    since = now.strftime("%Y-%m-%dT%H:%M:%S")

    with get_conn() as conn:
        if project:
            fresh = conn.execute(
                """SELECT * FROM radar_signals
                   WHERE project = ? AND status = 'new'
                     AND created_at >= datetime('now', ?)
                   ORDER BY signal_score DESC LIMIT 15""",
                (project.lower(), f"-{_LOOKBACK_HOURS} hours"),
            ).fetchall()
        else:
            fresh = conn.execute(
                """SELECT * FROM radar_signals
                   WHERE status = 'new'
                     AND created_at >= datetime('now', ?)
                   ORDER BY signal_score DESC LIMIT 15""",
                (f"-{_LOOKBACK_HOURS} hours",),
            ).fetchall()

    fresh = [dict(r) for r in fresh]
    top_mom = momentum.top_momentum(project, limit=12)

    # Bouw per momentum-signaal de merk-hoek (alleen als er een angle is).
    brand_sections = []
    for m in top_mom:
        sig = m  # top_momentum joined al de signaal-kolommen
        angle = sig.get("ai_angle") or ""
        if angle:
            brand_sections.append({
                "title": sig.get("title"),
                "url": sig.get("url"),
                "trend": m.get("trend"),
                "momentum_index": m.get("momentum_index"),
                "angle": angle,
                "project": sig.get("project"),
            })

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "date": now.strftime("%Y-%m-%d"),
        "project": project or "alle projecten",
        "fresh_count": len(fresh),
        "fresh": fresh,
        "momentum_count": len(top_mom),
        "momentum": top_mom,
        "brand_sections": brand_sections,
    }


def _render_markdown(d: Dict) -> str:
    lines = [
        "---",
        f"type: astros-digest",
        f"date: {d['date']}",
        f"project: \"{d['project']}\"",
        f"generated: {d['generated_at']}",
        "---",
        "",
        f"# 🛰️ Astros Digest — {d['date']}",
        "",
        f"*Concurrent-radar voor **{d['project']}** · gegenereerd {d['generated_at']}*",
        "",
        f"- 🆕 Nieuwe signalen (24u): **{d['fresh_count']}**",
        f"- 🚀 Signalen met momentum: **{d['momentum_count']}**",
        "",
        "## 🚀 Wat explodeert er nu (momentum)",
        "",
    ]
    if not d["momentum"]:
        lines.append("_Geen signalen met voldoende metingen voor een momentum-oordeel. "
                     "Na een paar scans verschijnt hier de echte trend._\n")
    else:
        for m in d["momentum"]:
            title = m.get("title") or "(geen titel)"
            url = m.get("url") or "#"
            trend = m.get("trend")
            mom = m.get("momentum_index")
            lines.append(f"### {trend.upper()} · momentum {mom} — [{title}]({url})")
            lines.append("")
            angle = m.get("ai_angle") or ""
            if angle:
                lines.append(f"**Merk-invalshoek:** {angle[:600]}")
                lines.append("")
            titles = m.get("ai_titles") or []
            if isinstance(titles, str):
                try:
                    titles = json.loads(titles)
                except Exception:
                    titles = []
            if titles:
                lines.append("**Titel-opties:** " + " · ".join(f'\"{t}\"' for t in titles[:3]))
                lines.append("")

    lines += ["", "## 🆕 Vers van de afgelopen 24 uur", ""]
    if not d["fresh"]:
        lines.append("_Geen nieuwe signalen in de laatste 24 uur._\n")
    else:
        for s in d["fresh"][:12]:
            title = s.get("title") or "(geen titel)"
            url = s.get("url") or "#"
            score = round(s.get("signal_score") or 0, 1)
            lines.append(f"- **{score}** · [{title}]({url}) "
                         f"— _{s.get('keyword','')}_")
        lines.append("")

    lines += [
        "",
        "## ➡️ Signal → Content (actielijst)",
        "",
        "Voor elk van bovenstaande signalen: open de Radar-tab, klik het "
        "signaal, en kies **AEO-aanval** (→ listicle + video + Reddit-concept) "
        "of **Obsidian** (→ trend-note in je vault).",
        "",
        "---",
        f"_Astros Digest · {d['generated_at']} · Agent OS_",
    ]
    return "\n".join(lines)


def write_daily_digest(project: Optional[str] = None) -> Optional[str]:
    """Bouw + schrijf het digest naar de Obsidian-vault. Retourneert het
    relatieve vault-pad (of None als de vault ontbreekt)."""
    d = build_digest(project)
    md = _render_markdown(d)
    if not OBSIDIAN_VAULT_PATH:
        log.warning("[astros] Geen OBSIDIAN_VAULT_PATH — digest niet naar vault geschreven")
        return None
    vault = Path(OBSIDIAN_VAULT_PATH)
    if not vault.exists():
        return None
    out_dir = vault / DIGEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{d['date']}.md" if not project else f"{d['date']}-{project.lower()}.md"
    path = out_dir / fname
    path.write_text(md, encoding="utf-8")
    rel = str(path.relative_to(vault))
    log.info("[astros] Digest geschreven: %s (%s nieuw, %s momentum)",
             rel, d["fresh_count"], d["momentum_count"])
    return rel
