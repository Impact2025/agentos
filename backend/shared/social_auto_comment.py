"""Auto-comment flow voor Facebook — de link-in-comment trick.

Na elke FB-post plaatst deze module automatisch de EERSTE reactie met de
CTA-link + hashtags. Waarom: Facebook onderdrukt bereik van posts mét uitgaande
link in de caption; een link in de eerste reactie houdt organisch bereik hoog
én geeft de CTA. Legaal, geen TOS-schending.

Gebruik:
  await auto_comment_after_post(post_id, site_name, project, age_label)
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Per leeftijdsdoelgroep de juiste CTA-link (leeftijd-specifieke landingspagina).
# Valt terug op de hoofd-URL als er geen specifieke pagina is.
CTA_LINKS = {
    "30+": "https://datingassistent.nl/dating-voor-30-plussers",
    "40+": "https://datingassistent.nl/dating-voor-40-plussers",
    "50+": "https://datingassistent.nl/dating-voor-50-plussers",
}
DEFAULT_CTA = "https://datingassistent.nl/registreren"

HASHTAGS = "#DatenZonderGedoe #Datening #RelatieAdvies #DatingVoor30Plus"


async def auto_comment_after_post(post_id: str, site_name: str,
                                  age_label: Optional[str] = None) -> dict:
    """Plaats de first-comment (CTA-link + hashtags) onder een FB-post.

    post_id: het id van de zojuist geplaatste post (formaat pageid_postid).
    site_name: AgentOS site-naam (voor token-selectie).
    age_label: '30+' / '40+' / '50+' -> bepaalt de CTA-link.
    """
    from . import facebook as fb

    link = CTA_LINKS.get(age_label or "", DEFAULT_CTA)
    text = (
        f"Meer herkenning of klaar voor échte matches? "
        f"Start je gratis profiel: {link}\n\n"
        f"{HASHTAGS}"
    )
    res = await fb.comment_on_post(post_id, text, site_name=site_name)
    if res.get("success"):
        logger.info("✅ Auto-comment geplaatst op %s (%s)", post_id, age_label)
    else:
        logger.warning("⚠️ Auto-comment mislukt op %s: %s", post_id, res.get("error"))
    return res
