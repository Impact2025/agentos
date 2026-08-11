"""Cross-site linkbuilding — PBN-veilig (Goldie's 5-site flywheel, maar legitiem).

Goldie's grootste hefboom is cross-site linking tussen 5 domeins. Wij hebben 13
echte, onafhankelijke merk-sites die nu NIET naar elkaar linken — de grootste
onbenutte ranking-hefboom in het portfolio. Dit module trekt die hefboom, maar
met de veiligheidsregels die Goldie zélf noemt als PBN-risico:

  1. ALLEEN met expliciete toestemming: een site linkt alleen naar zusters die
     Vincent heeft toegevoegd aan een cluster (tabel cross_site_clusters).
     Geen automatische "link alles naar alles".
  2. ALLEEN contextueel relevant: match op gedeelde entiteiten/keywords tussen
     de artikelen, niet willekeurig. Een lot-link is precies wat Google als
     linknetwerk markeert.
  3. MAX 2 cross-site links per artikel, nooit naar de eigen site.
  4. ALLEEN inline in de body — geen sitewide header/footer-links (die zijn de
     klassieke PBN-voetafdruk).

De functie cross_site_candidates() wordt aangeroepen vanuit article_writer._link_candidates()
en voegt relevante zuster-artikelen toe aan de candidate-lijst; de bestaande
strip_unvetted_internal_links + insert_link-logica doet de rest (en blijft de
eigen-site-links beschermen).
"""

from typing import Dict, List, Set
from difflib import SequenceMatcher

from ...shared.database import get_conn

# Hoeveel cross-site kandidaten we maximaal teruggeven (de linkstap kiest er
# daarna zelf ≤2 uit op basis van anker-matching in de body).
MAX_CROSS_CANDIDATES = 6

# Minimaal lexicale overlap (0..1) tussen zoekwoord/tekst en de zuster-titel
# voordat we een link "relevant" noemen. Lager = losser, hoger = strikter.
RELEVANCE_THRESHOLD = 0.18


def _cluster_sites(site_id: str) -> List[str]:
    """Site-IDs waarmee `site_id` mag cross-linken (exclusief zichzelf)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT peer_site_id FROM cross_site_clusters WHERE site_id = ?",
            (site_id,),
        ).fetchall()
    return [r["peer_site_id"] for r in rows if r["peer_site_id"] != site_id]


def _published_articles(site_id: str, own_host: str) -> List[Dict]:
    """Gepubliceerde artikelen van een zuster-site, als (url, title, slug)."""
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            """SELECT p.url, p.title, p.slug, s.base_url
               FROM published_pages p
               JOIN sites s ON s.id = p.site_id
               WHERE p.site_id = ? AND p.url IS NOT NULL AND p.url != ''""",
            (site_id,),
        ).fetchall()
    out = []
    for r in rows:
        url = (r["url"] or "").strip()
        if not url or own_host in url.lower():
            continue  # nooit naar jezelf
        out.append({"url": url, "title": (r["title"] or "").strip()})
    return out


def _relevance(anchor_text: str, candidate_title: str) -> float:
    """Lexicale overlap tussen het artikel-onderwerp en de zuster-titel."""
    a = (anchor_text or "").lower()
    b = (candidate_title or "").lower()
    if not a or not b:
        return 0.0
    # Token-overlap: gedeelde woorden wegen zwaarder dan substring-matches.
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if a_tokens and b_tokens:
        jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    else:
        jaccard = 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    return max(jaccard, seq * 0.5)


def cross_site_candidates(site: Dict, keyword: str, text: str = "") -> List[Dict[str, str]]:
    """Relevante gepubliceerde artikelen van zuster-sites (PBN-veilig gefilterd).

    Returns een lijst van {"url", "title"} — exact hetzelfde shape als
    _link_candidates, zodat de caller ze aan de candidate-lijst kan hangen.
    Bij een site zonder cluster, of zonder relevante matches, een lege lijst.
    """
    site_id = site.get("id")
    if not site_id:
        return []
    peers = _cluster_sites(site_id)
    if not peers:
        return []

    own_host = (site.get("base_url") or "").rstrip("/").lower().split("://")[-1].replace("www.", "")
    anchor = f"{keyword} {text[:400]}".strip()

    scored: List[Dict] = []
    seen_urls: Set[str] = set()
    for peer_id in peers:
        for art in _published_articles(peer_id, own_host):
            url = art["url"].rstrip("/")
            if url in seen_urls:
                continue
            rel = _relevance(anchor, art["title"])
            if rel < RELEVANCE_THRESHOLD:
                continue
            seen_urls.add(url)
            scored.append({"url": art["url"], "title": art["title"], "_rel": rel})

    scored.sort(key=lambda x: x["_rel"], reverse=True)
    return [{"url": c["url"], "title": c["title"]} for c in scored[:MAX_CROSS_CANDIDATES]]


def add_cross_site_link(site_id: str, peer_site_id: str) -> bool:
    """Voeg een bidirectionele cluster-relatie toe (Vincent's allowlist)."""
    if site_id == peer_site_id:
        return False
    with get_conn() as conn:
        for a, b in ((site_id, peer_site_id), (peer_site_id, site_id)):
            conn.execute(
                "INSERT OR IGNORE INTO cross_site_clusters (site_id, peer_site_id) VALUES (?, ?)",
                (a, b),
            )
    return True


def list_cross_site_links(site_id: str) -> List[Dict]:
    """Toon de huidige cluster-relaties van een site (voor de UI/audit)."""
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            """SELECT c.peer_site_id, s.name, s.base_url
               FROM cross_site_clusters c
               JOIN sites s ON s.id = c.peer_site_id
               WHERE c.site_id = ? ORDER BY s.name""",
            (site_id,),
        ).fetchall()
    return [dict(r) for r in rows]
