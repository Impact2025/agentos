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
    "30+": "https://datingassistent.nl/30-plus",
    "40+": "https://datingassistent.nl/40-plus",
    "50+": "https://datingassistent.nl/50-plus",
}
DEFAULT_CTA = "https://datingassistent.nl/quiz"

# Leeftijdsspecifieke hashtags — voorkomt de P0-bug waarbij #DatingVoor30Plus
# op een 40+/50+-pagina terechtkwam. Per leeftijd een eigen slot-tag.
AGE_HASHTAGS = {
    "30+": "#DatenZonderGedoe #Datening #RelatieAdvies #DatingVoor30Plus",
    "40+": "#DatenZonderGedoe #Datening #RelatieAdvies #40PlusDaten",
    "50+": "#DatenZonderGedoe #Datening #RelatieAdvies #50PlusDaten",
}
DEFAULT_HASHTAGS = "#DatenZonderGedoe #Datening #RelatieAdvies #DatingAssistent"


async def auto_comment_after_post(post_id: str, site_name: str,
                                  age_label: Optional[str] = None,
                                  cta_text: Optional[str] = None,
                                  token_override: Optional[str] = None) -> dict:
    """Plaats de first-comment (CTA-link + hashtags) onder een FB-post.

    post_id: het id van de zojuist geplaatste post (formaat pageid_postid).
    site_name: ImpactOS site-naam (voor token-selectie).
    age_label: '30+' / '40+' / '50+' -> bepaalt de CTA-link (alleen als cta_text leeg).
    cta_text: volledige eerste-reactie-tekst uit de post zelf (copy_json['cta']).
              Als die er is, wint die — zo kunnen campagne-posts hun eigen
              leeftijd-specifieke landingspagina/quiz-link gebruiken i.p.v. de
              hard-coded DEFAULT_CTA.
    token_override: expliciet page-token als `site_name` niet naar de pagina
              resolveert waar `post_id` op staat (bv. DatingAssistent-doelgroep-
              pagina's zonder eigen sites-rij, zie scripts/da_post_engine.py).
    """
    from . import facebook as fb

    if cta_text and cta_text.strip():
        text = cta_text.strip()
    else:
        link = CTA_LINKS.get(age_label or "", DEFAULT_CTA)
        tags = AGE_HASHTAGS.get(age_label or "", DEFAULT_HASHTAGS)
        # UTM conform de universele social-post standaard (meetbaarheid).
        link = fb.build_utm_url(link, "facebook", site_name)
        text = (
            f"Meer herkenning of klaar voor échte matches? "
            f"Start je gratis profiel: {link}\n\n"
            f"{tags}"
        )
    res = await fb.comment_on_post(post_id, text, site_name=site_name, token_override=token_override)
    if res.get("success"):
        logger.info("✅ Auto-comment geplaatst op %s (%s)", post_id, age_label)
        logger.info("⚠️  PIN deze eerste reactie handmatig in Facebook "
                    "(⋯ → 'Vastmaken aan de bovenkant'). De Graph API kan niet pinnen. "
                    "En reageer binnen 1 uur op alle replies (standaard regel 3).",
                    post_id)
    else:
        logger.warning("⚠️ Auto-comment mislukt op %s: %s", post_id, res.get("error"))
    return res
