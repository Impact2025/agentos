"""Learning-loop voor onder-de-grens content.

Wanneer een artikel na de maximale verbeterrondes nog steeds onder
CONTENT_MIN_SCORE zit, leert de agent er expliciet van: de reviewer-feedback
en het zoekwoord worden weggeschreven naar de Obsidian-vault
(10_Projects/_lessons/onder-85.md), zodat toekomstige generaties het patroon
kennen en het niet opnieuw fout doen.

Dit is een "leren van fouten"-spiraal: niet publiceerbaar → analyseer waarom →
bewaar de les → volgende keer scherper beginnen. De mens ziet deze artikelen
nooit op het dashboard; de agent repareert ze zelf of leert ervan.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Doelmap in de Obsidian-vault (relatief t.o.v. de vault-root in config).
_LESSONS_REL = "10_Projects/_lessons/onder-85.md"


def _vault_root() -> Optional[str]:
    # De Agent OS vault-path komt uit de omgeving (zie ook article_writer.py,
    # dat OBSIDIAN_VAULT_PATH gebruikt voor de url-register-sync).
    import os
    return os.getenv("OBSIDIAN_VAULT_PATH") or os.getenv("VAULT_ROOT") or None


def _lessons_path() -> Optional[str]:
    root = _vault_root()
    if not root:
        return None
    import os
    return os.path.join(root, _LESSONS_REL)


def record_under85(site: Dict, keyword: str, review: Dict) -> bool:
    """Schrijf een les over een onder-de-grens artikel naar de vault.

    `review` = {"score": int, "feedback": str}. Retourneert True als weggeschreven.
    """
    try:
        path = _lessons_path()
        if not path:
            logger.debug("[learning] Geen VAULT_ROOT — onder-85 les niet bewaard.")
            return False
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)

        score = review.get("score", 0)
        feedback = (review.get("feedback") or "—").strip()
        site_name = (site or {}).get("name", "?")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        note = (
            f"\n## {ts} · {site_name} · '{keyword}' (score {score})\n"
            f"\n**Waarom onder de grens:**\n\n{feedback}\n"
            f"\n**Patroon / les:** artikel haalde na maximale verbeterrondes "
            f"nog steeds {score}/100. Bij een volgende generatie voor '{keyword}' "
            f"(of soortgelijke hoek) scherper beginnen op bovenstaande feedback "
            f"— zeker E-E-A-T, direct-antwoord en FAQ-sectie.\n"
            f"\n---\n"
        )

        # Append (maak aan als het nog niet bestaat, met een kop).
        header = (
            "# Lessen uit onder-de-grens content (< kwaliteitsgrens)\n"
            "\n> Automatisch bijgehouden door de content-pipeline. Elke keer dat een\n"
            "> artikel na de verbeterrondes onder de grens blijft, leert de agent er\n"
            "> hier van. De mens ziet deze artikelen niet op het dashboard.\n"
        )
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(header + "\n")
        with open(path, "a", encoding="utf-8") as f:
            f.write(note)

        logger.info("[learning] Onder-85 les bewaard voor '%s' (%s) → %s", keyword, score, path)
        return True
    except Exception as e:
        logger.warning("[learning] Bewaren onder-85 les mislukt: %s", str(e)[:160])
        return False
