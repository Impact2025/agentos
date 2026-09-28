"""Doelgroepcheck van de kwaliteitsgate (28 sep 2026).

Echte queries uit Search Console van WeAreImpact: notariaat-zoekers rankten op
positie 2-4, maar zijn geen doelgroep. De regel mag daarentegen nooit een echte
kans in het sociaal domein raken.
"""
from backend.domains.seo import opportunity_quality as oq

WAI = {"name": "WeAreImpact", "base_url": "https://weareimpact.nl", "profile": ""}


def _reden(query, site=WAI):
    res = oq._junk_reason(query, site)
    return res[0] if res else None


def test_notariaat_is_buiten_doelgroep():
    for q in ("ai akten opstellen notariaat", "ai werkplek notariaat",
              "hoe kunnen notarissen ai gebruiken", "ai roadmap notariskantoor"):
        assert _reden(q) == "buiten-doelgroep", q


def test_burgervraag_en_besmetting():
    assert _reden("wie heeft recht op wmo ondersteuning") == "buiten-doelgroep"
    assert _reden("hond adopteren antwerpen") == "buiten-doelgroep"
    assert _reden("cadeaus voor koppels") == "buiten-doelgroep"


def test_echte_kansen_blijven_door():
    for q in ("consultant sociaal domein", "kwartiermaker regionale jeugdzorg",
              "programmamanager digitale transformatie", "interim directeur welzijn",
              "honderd vrijwilligers managen", "eerste 100 dagen interim directeur"):
        assert _reden(q) is None, q


def test_andere_site_wordt_niet_geraakt():
    assert _reden("ai akten opstellen notariaat",
                  {"name": "NotarisTool", "base_url": "https://x.nl", "profile": ""}) is None


def test_uitsluiting_via_siteprofiel():
    site = {"name": "Andere Site", "base_url": "https://x.nl",
            "profile": "Wij helpen X.\nBuiten doelgroep: tandarts, orthodont\nMeer tekst."}
    assert _reden("ai voor de tandartspraktijk", site) == "buiten-doelgroep"
    assert _reden("ai voor de huisarts", site) is None
