"""Agent-identiteitslaag — single source of truth voor code, UI én marketing.

Elke "agent" in ImpactOS heeft precies één record in deze roster. De expert-
profielen in `agent_profiles` blijven de operationele uitvoerders (rollen),
maar de roster legt vast WIE de buitenwereld ziet: naam, gezicht, laag, en
welke profielen onder dat gezicht hangen.

Drie lagen:
  - face   : marketing-gezicht (Iris, Mara, Bram, Noor) — wat klanten zien
  - crew   : interne operationele agent (Toby, AI Diary) — geen marketing
  - role   : anonieme expert-profielen (SEO Copywriter, ...) — backstage

Efficiëntie-principe: 1 bron, 0 drift. Code, UI en copy halen hier allemaal
uit — er bestaan geen losse "naam" strings meer in de codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict

# ── Marketing-cast ────────────────────────────────────────────────────────────
# Gezichten die klanten te zien krijgen. Elk gezicht dekt een cluster van
# expert-profielen (geen 14 losse namen → geen brand-dilutie).
FACES: Dict[str, "Face"] = {}


@dataclass
class Face:
    key: str
    name: str
    title: str
    tagline: str
    layer: str = "face"
    covers_profiles: List[str] = field(default_factory=list)
    emoji: str = ""  # UI-only, nooit in app-copy
    bio: str = ""
    reports_to: str = ""  # wie stuurt deze agent aan (manager-relatie)


def _build_faces() -> Dict[str, Face]:
    faces = {
        "iris": Face(
            key="iris",
            name="Iris",
            title="AI Manager",
            tagline="Houdt overzicht, informeert je en stuurt de agents aan — jij hoeft niet alles te weten.",
            emoji="🧭",
            reports_to="",  # Iris IS de manager
            covers_profiles=[],  # Iris is een UI/orchestratie-concept (action_center), geen profiel
            bio="Iris is je AI-manager. Elke ochtend leest ze je hele portfolio uit, "
                "duidt de cijfers en geeft je in één oogopslag wat er écht gebeurd is "
                "en wat de volgende zet is. Ze houdt overzicht over alle projecten, "
                "informeert je proactief over wat aandacht vraagt, en zet Mara, Bram "
                "en Noor aan het werk op de pijlers waar de grootste hefboom zit. "
                "Geen ruis, geen dashboards om doorheen te worstelen — Iris regelt het.",
        ),
        "mara": Face(
            key="mara",
            name="Mara",
            title="Content & Marketing Lead",
            tagline="Zorgt dat je gevonden wordt — van SEO-artikel tot social post.",
            emoji="✍️",
            reports_to="iris",
            covers_profiles=[
                "SEO Copywriter", "SEO Editor", "Content Editor", "Content Judge",
                "Social Media Copywriter", "Video Director",
            ],
            bio="Mara vertaalt je expertise naar vindbare content. Ze schrijft "
                "Nederlandse SEO-artikelen die bovenaan Google staan, optimaliseert "
                "ze tot ze scoren, en hergebruikt ze als scherpe social posts. "
                "Werkt onder aansturing van Iris.",
        ),
        "bram": Face(
            key="bram",
            name="Bram",
            title="Outreach Lead",
            tagline="Vindt de juiste organisaties en benadert ze warm en oprecht.",
            emoji="🤝",
            reports_to="iris",
            covers_profiles=[
                "Lead Prospect Researcher", "Outreach Copywriter", "Outreach Beoordelaar", "Email Manager",
            ],
            bio="Bram scant de Nederlandse markt voor klanten die écht passen, en "
                "schrijft outreach die klinkt als een mens — niet als een massamail. "
                "Elk bericht wordt eerst beoordeeld op toon en AVG voor het de deur uit gaat. "
                "Werkt onder aansturing van Iris.",
        ),
        "noor": Face(
            key="noor",
            name="Noor",
            title="Analist",
            tagline="Cijfers worden besluiten — geen grafieken om naar te staren.",
            emoji="📊",
            reports_to="iris",
            # GEO Specialist hoort hier alleen — hij stond eerder ook bij Mara,
            # waardoor _face_for_profile() en de DB (`face_key`) uit elkaar
            # liepen (21 aug 2026). Eén profiel = één face.
            covers_profiles=["Analytics Analist", "Radar Trend-Analist", "Vacature Fit-Analist", "GEO Specialist"],
            bio="Noor vertaalt GA4, Search Console en markttrends naar de drie dingen "
                "die je moet doen. En ze houdt je vacature-fit scherp, zodat je alleen "
                "opdrachten ziet die de moeite waard zijn. Werkt onder aansturing van Iris.",
        ),
    }
    return faces


# ── Interne crew (geen marketing-gezicht) ──────────────────────────────────────
CREW = {
    "toby": {
        "key": "toby",
        "name": "Toby",
        "title": "Workforce Watchdog",
        "layer": "crew",
        "tagline": "Houdt de infrastructuur in de gaten en tikt je op de schouder vóór er iets misgaat.",
    },
    "ai_diary": {
        "key": "ai_diary",
        "name": "AI Diary",
        "title": "Context Engine",
        "layer": "crew",
        "tagline": "Rolt je notes en spraak op tot de context waar de agents op draaien.",
    },
}

# ── Orchestrator (alleen tonen wanneer daadwerkelijk live) ─────────────────────
ORCHESTRATOR = {
    "key": "simon",
    "name": "Simon",
    "title": "Chief of Staff",
    "layer": "orchestrator",
    "tagline": "Routeert taken naar de juiste agent en escaleert naar jou bij kritieke beslissingen.",
    "visible_when_live": True,
}


def get_faces() -> List[Face]:
    """De marketing-cast, op volgorde van belangrijkheid."""
    if not FACES:
        FACES.update(_build_faces())
    return [FACES["iris"], FACES["mara"], FACES["bram"], FACES["noor"]]


def get_face(key: str) -> Face | None:
    if not FACES:
        FACES.update(_build_faces())
    return FACES.get(key)


def all_known_agents() -> List[dict]:
    """Volledige lijst (faces + crew + orchestrator) voor UI/overzichten."""
    out = [f.__dict__ for f in get_faces()]
    out += list(CREW.values())
    out.append(ORCHESTRATOR)
    return out


def marketing_team_block() -> str:
    """Kant-en-klare, neutrale marketing-tekst (geen emoji) voor landingpages."""
    lines = ["Ontmoet je AI-team:", ""]
    for f in get_faces():
        lines.append(f"{f.name} — {f.title}: {f.tagline}")
    return "\n".join(lines)


if __name__ == "__main__":
    for f in get_faces():
        print(f"{f.name} · {f.title} · dekt: {', '.join(f.covers_profiles) or '(UI)'}")
    print()
    print(marketing_team_block())
