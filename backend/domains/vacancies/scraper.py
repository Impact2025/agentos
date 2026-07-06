"""
Generieke pagina-tekst-scraper voor vacatureteksten.

In tegenstelling tot prospecting/scraper.py (NAW-extractie) halen we hier alleen de
leesbare paginatekst op, als extra context voor de AI fit-analyse. Veel jobboards
(LinkedIn, Indeed) blokkeren scraping of laden content via JS - dit faalt dan stil
(lege string) en de analyse valt terug op de Tavily-snippet.
"""
import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'nl-NL,nl;q=0.9,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}


def scrape_text(url: str, max_chars: int = 4000) -> str:
    """Haal de leesbare tekst van een vacaturepagina op. Faalt stil bij blokkade."""
    if not url or not url.startswith('http'):
        return ""
    try:
        with httpx.Client(
            headers=HEADERS, timeout=12.0, follow_redirects=True, verify=False,
        ) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, 'lxml')
            for tag in soup(['script', 'style', 'noscript', 'svg', 'img', 'nav', 'footer']):
                tag.decompose()
            text = soup.get_text(' ', strip=True)
            return text[:max_chars]
    except Exception as e:
        log.debug('[vacancies-scraper] %s: %s', url, e)
        return ""
