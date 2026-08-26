"""
Sites — portfolio van websites voor de Demand Engine.

Elke site koppelt een Search Console-property (databron) aan een publicatie-doel
(jouw eigen blog-admin, ingevuld in Fase 4). Voor Fase 1 zijn alleen `name` en
`gsc_property` nodig.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn

_FIELDS = ("name", "base_url", "gsc_property", "publish_api_url", "publish_api_key", "default_author",
           "linkedin_token", "linkedin_user_urn",
           "facebook_page_id", "facebook_page_token", "instagram_business_id",
           "twitter_api_key", "twitter_api_secret", "twitter_access_token", "twitter_access_secret",
           "auto_content_enabled", "external_db_url", "ga4_property_id",
           "profile", "ctas", "content_batch_size", "indexnow_key", "content_schedule",
           "paused")

# Secret velden die nooit kaal naar de frontend mogen — elk krijgt i.p.v. de waarde
# een "<veld>_set" boolean terug (zelfde patroon als publish_api_key/linkedin_token).
_SECRET_FIELDS = (
    "publish_api_key", "linkedin_token",
    "facebook_page_token", "twitter_api_key", "twitter_api_secret",
    "twitter_access_token", "twitter_access_secret", "external_db_url",
    "indexnow_key",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(row: Dict) -> Dict:
    """Stuur secret-velden (publicatie-/platform-tokens) nooit kaal naar de frontend."""
    d = dict(row)
    for field in _SECRET_FIELDS:
        val = d.get(field) or ""
        d[f"{field}_set"] = bool(val)
        d.pop(field, None)
    return d


def list_sites() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY created_at ASC").fetchall()
    return [_redact(r) for r in rows]


def get_site(site_id: str) -> Optional[Dict]:
    """Volledige rij (incl. sleutel) — voor interne services zoals de Demand Engine."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return dict(row) if row else None


def find_site_by_project(project: str) -> Optional[Dict]:
    """Omgekeerde van `_project_for_job` (orchestrator): projectnaam → site-rij.

    Squash-vergeleken (zie shared/projects.py) — `sites.name` en de projectnaam
    die andere domeinen gebruiken kennen dezelfde spatie/hoofdletter-varianten.
    Volledige rij (incl. sleutel), voor interne services.
    """
    from ...shared.projects import squash_project
    doel = squash_project(project)
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sites").fetchall()
    for r in rows:
        if squash_project(r["name"] or "") == doel:
            return dict(r)
    return None


def better_matching_site(title: str, current_site_id: str) -> Optional[Dict]:
    """Hoort `title` méétbaar beter bij een ándere site dan bij `current_site_id`?

    Zelfde deterministische woordoverlap-toets als `iris/integrity.py:
    content_hoort_bij_andere_site` (de site-woordenschat = profiel + koppen die
    er al live staan), maar hier herbruikt om vóóraf te weigeren in plaats van
    achteraf te melden. Eén antwoord op "hoort dit bij deze site?" — de audit
    en deze gate roepen dezelfde functie aan, anders lopen ze uiteen zoals
    `is_same_topic` elders al moest voorkomen.

    Retourneert de betere site (volledige rij) alleen bij een dúídelijke
    winnaar — zie de kwantificatie hieronder. Een nieuw onderwerp op de
    eigen site moet gewoon mogen; de gate mag géén goede content weigeren
    op basis van toevallige, generieke woordoverlap.

    Bug-historie (19 aug 2026): de oorspronkelijke toets telde elke
    titel-woord dat ook in een ándere site's woordenschat zat (profiel +
    gepubliceerde titels). Dat leverde valse positieven op: "Dierenasiel in
    de buurt: zo vind je de beste match" werd naar Vrijwilligersmatch
    gestuurd, terwijl dierenasiel = asieldier = 100% Pootgelukkig-terrein
    (3 asiel-artikelen al live op pootgelukkig.nl). De woorden "buurt",
    "zo", "vind", "match" komen wél voor in Vrijwilligersmatch's
    gepubliceerde titels, maar zijn generiek en óók (deels) in Pootgelukkigs
    eigen woordenschat. "Volwassen katten ter adoptie" werd zelfs naar Daar
    gestuurd — willekeurig, op "beste"/"keuze". Beide artikelen hóren bij
    Pootgelukkig.

    Fix (deze versie):
      • Stopwoorden ("zo", "vind", "beste", "buurt", "keuze", "in", "de", …)
        tellen niet mee — ze zeggen niets over het onderwerp.
      • We tellen de EXCLUSIEVE overlap: een titel-woord telt alleen mee als
        het in de kandidaat-site zit ÉN niet in de eigen site. Een woord dat
        ook in de eigen site voorkomt ("match" bij Pootgelukkig) is geen
        bewijs vóór de andere site.
      • Drempel: ≥3 exclusieve woorden én significant meer dan de eigen site
        (beste_n ≥ 2 × mijn + 1). Pas dan is er sprake van een duidelijke,
        onderwerp-specifieke match en mogen we weigeren.
    `None` als de huidige site onbekend is of als er geen betere kandidaat is.
    """
    from ..iris.integrity import _woorden

    # Generieke woorden die in tientallen sites voorkomen en dus niets over
    # het onderwerp zeggen. Zonder deze filter schoof "Dierenasiel in de
    # buurt … beste match" ten onrechte naar Vrijwilligersmatch.
    _STOP = {
        "de", "het", "een", "en", "in", "op", "van", "voor", "met", "aan",
        "bij", "zo", "vind", "vindt", "je", "jij", "is", "zijn", "dat",
        "die", "dit", "deze", "deze", "niet", "of", "om", "te", "tot",
        "als", "wat", "hoe", "waar", "waarom", "waardoor", "door", "met",
        "naar", "on", "onder", "over", "uit", "bij", "rond", "tussen",
        "beste", "goede", "goed", "slechte", "keuze", "keuzes", "stappen",
        "manieren", "tips", "tip", "top", "complete", "gids", "zo", "zoals",
        "echt", "wel", "niet", "altijd", "ooit", "ooit", "maakt", "maak",
        "doe", "doen", "helpt", "helpen", "7", "8", "9", "10", "buurt",
        "mijn", "deze", "die", "zelf", "zelfde", "andere", "eerste",
        "laatste", "nieuwe", "nieuw", "kleine", "grote", "goedkope",
        "duurste", "alle", "elke", "ieder", "wie", "wat", "waar", "wanneer",
        "waarom", "waarmee", "waarvoor", "krijgt", "krijgen", "geeft",
        "geven", "zorgt", "zorgen", "voelt", "voelen", "weet", "weten",
    }

    def _filter(stop_set, words):
        return {w for w in words if w not in stop_set and len(w) > 2}

    with get_conn() as conn:
        sites = conn.execute(
            "SELECT id, name, COALESCE(profile, '') AS profile FROM sites "
            "WHERE COALESCE(is_test, 0) = 0"
        ).fetchall()
        gepubliceerd = conn.execute(
            "SELECT site_id, title FROM content_jobs WHERE status = 'published'"
        ).fetchall()

    schat: Dict[str, set] = {}
    namen: Dict[str, str] = {}
    for s in sites:
        schat[s["id"]] = _filter(_STOP, _woorden(s["name"] + " " + s["profile"]))
        namen[s["id"]] = s["name"]
    for r in gepubliceerd:
        if r["site_id"] in schat:
            schat[r["site_id"]] |= _filter(_STOP, _woorden(r["title"]))

    if current_site_id not in schat:
        return None
    titel = _filter(_STOP, _woorden(title))
    if len(titel) < 3:
        return None
    mijn = len(titel & schat[current_site_id])
    beste, beste_n = None, 0
    for sid, woorden in schat.items():
        if sid == current_site_id:
            continue
        # Exclusieve overlap: alleen woorden die NIET ook in de eigen site
        # zitten tellen als bewijs vóór de kandidaat-site. Een woord dat de
        # eigen site óók heeft ("match" bij Pootgelukkig) is geen reden om
        # het artikel naar een ander te sturen.
        n = len(titel & woorden - schat[current_site_id])
        if n > beste_n:
            beste, beste_n = sid, n
    # Duidelijke, onderwerp-specifieke winnaar vereist: ≥3 exclusieve woorden
    # én significant meer dan de eigen site. Anders: artikel hoort bij de
    # huidige site, publiceer gewoon.
    if beste and beste_n >= 3 and beste_n >= 2 * mijn + 1:
        return get_site(beste)
    return None


def create_site(data: Dict) -> Dict:
    site_id = str(uuid.uuid4())
    now = _now()
    values = {f: (data.get(f) or "") for f in _FIELDS}
    if not values["name"].strip():
        raise ValueError("Veld 'name' is verplicht.")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sites
               (id, name, base_url, gsc_property, publish_api_url, publish_api_key,
                default_author, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (site_id, values["name"], values["base_url"], values["gsc_property"],
             values["publish_api_url"], values["publish_api_key"],
             values["default_author"], now),
        )
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return _redact(row)


def update_site(site_id: str, data: Dict) -> Optional[Dict]:
    updates, params = [], []
    for f in _FIELDS:
        if f in data and data[f] is not None:
            # Lege secret-velden = niet overschrijven (behoud bestaande waarde).
            if f in _SECRET_FIELDS and not str(data[f]).strip():
                continue
            updates.append(f"{f} = ?")
            params.append(data[f])
    if not updates:
        return get_site(site_id) and _redact(get_site(site_id))
    params.append(site_id)
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE sites SET {', '.join(updates)} WHERE id = ?", params)
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return _redact(row)


def delete_site(site_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return cur.rowcount > 0
