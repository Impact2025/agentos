"""
Website scraper voor lead-verrijking.
Extraheert NAW-gegevens uit zakelijke websites via tel:/mailto:-links,
Schema.org microdata, regex-patronen en contact-paginadetectie.
"""
import re
import logging
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── Regex-patronen ────────────────────────────────────────────────────────────

_PHONE_RE = re.compile(
    r'(?:'
    r'\+31[\s\.\-]?(?:\(0\)[\s\.\-]?)?[1-9][\d\s\.\-]{6,12}'   # +31 notatie
    r'|0[1-9]\d[\s\.\-]?\d{3}[\s\.\-]?\d{4}'                    # 0XX-XXX XXXX
    r'|06[\s\.\-]?\d{8}'                                          # 06-XXXXXXXX
    r'|0800[\s\.\-]?\d{4}'                                        # gratis 0800
    r')'
)
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,6}')
_KVK_RE = re.compile(r'[Kk][Vv][Kk][^\d]{0,8}(\d{8})')
_BTW_RE = re.compile(r'[Bb][Tt][Ww][^\d]{0,5}(NL\d{9}B\d{2})', re.IGNORECASE)
_POSTAL_RE = re.compile(r'\b(\d{4}\s?[A-Z]{2})\b')
_STREET_RE = re.compile(
    r'([A-Z][a-zé\-]+'
    r'(?:straat|laan|weg|plein|kade|dijk|gracht|hof|park|singel|dreef|steeg|pad|dam|markt|allee|boulevard|lane)'
    r'[\s,]*\d{1,5}\s*(?:[a-zA-Z-]{0,5})?)',
    re.UNICODE,
)

_CONTACT_HINTS = ['contact', 'contactgegevens', 'bereikbaarheid', 'over-ons', 'locatie', 'adres', 'vestiging']
_NOISE_EMAILS = {'noreply', 'no-reply', 'donotreply', 'example', 'privacy@', 'webmaster@', 'info@example'}

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_phone(raw: str) -> str:
    digits_plus = re.sub(r'[^\d+]', '', raw)
    if digits_plus.startswith('+31'):
        return '0' + digits_plus[3:]
    return digits_plus[:13]


def _is_noise_email(email: str) -> bool:
    e = email.lower()
    return any(n in e for n in _NOISE_EMAILS) or e.endswith('.png') or e.endswith('.jpg')


def _extract_from_html(html: str, result: Dict) -> None:
    """Vult result in-place met NAW-data uit HTML."""
    soup = BeautifulSoup(html, 'lxml')

    # 1. Schema.org (rijkste bron)
    for org in soup.find_all(attrs={'itemtype': re.compile(r'schema\.org/(Organization|LocalBusiness)')}):
        tel = org.find(attrs={'itemprop': 'telephone'})
        if tel and not result['phone']:
            result['phone'] = _normalise_phone(tel.get_text(strip=True))

        em = org.find(attrs={'itemprop': 'email'})
        if em and not result['email']:
            result['email'] = em.get_text(strip=True).lower()

        addr = org.find(attrs={'itemprop': 'address'})
        if addr:
            st = addr.find(attrs={'itemprop': 'streetAddress'})
            ci = addr.find(attrs={'itemprop': 'addressLocality'})
            pc = addr.find(attrs={'itemprop': 'postalCode'})
            if st and not result['address']:
                result['address'] = st.get_text(strip=True)
            if ci and not result['city']:
                result['city'] = ci.get_text(strip=True)
            if pc and not result['postal_code']:
                result['postal_code'] = pc.get_text(strip=True).upper()

    # 2. tel: hyperlinks
    for a in soup.find_all('a', href=re.compile(r'^tel:')):
        raw = a['href'][4:].strip()
        if raw and not result['phone']:
            result['phone'] = _normalise_phone(raw)
            break

    # 3. mailto: hyperlinks
    for a in soup.find_all('a', href=re.compile(r'^mailto:')):
        email = a['href'][7:].split('?')[0].strip().lower()
        if email and not _is_noise_email(email) and not result['email']:
            result['email'] = email
            break

    # 4. <address> tag
    addr_tag = soup.find('address')
    if addr_tag and not result.get('address_raw'):
        result['address_raw'] = addr_tag.get_text(' ', strip=True)[:250]

    # Verwijder ruis vóór tekst-extract
    for tag in soup(['script', 'style', 'noscript', 'svg', 'img']):
        tag.decompose()

    text = soup.get_text(' ', strip=True)

    # 5. Regex op volledige tekst
    if not result['phone']:
        m = _PHONE_RE.search(text)
        if m:
            result['phone'] = _normalise_phone(m.group())

    if not result['email']:
        for m in _EMAIL_RE.finditer(text):
            email = m.group().lower()
            if not _is_noise_email(email):
                result['email'] = email
                break

    if not result['kvk_number']:
        m = _KVK_RE.search(text)
        if m:
            result['kvk_number'] = m.group(1)

    if not result['postal_code']:
        m = _POSTAL_RE.search(text)
        if m:
            result['postal_code'] = m.group(1).upper()

    if not result['address']:
        m = _STREET_RE.search(text)
        if m:
            result['address'] = m.group(1).strip()

    # 6. Stad afleiden uit context rondom postcode
    if result['postal_code'] and not result['city']:
        idx = text.find(result['postal_code'])
        if idx >= 0:
            after = text[idx + len(result['postal_code']):idx + len(result['postal_code']) + 50]
            cm = re.match(r'[\s,]+([A-Z][a-zA-Z\s\-]{2,25})', after)
            if cm:
                result['city'] = cm.group(1).strip()

    result['page_text'] = text[:4500]


def _find_contact_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    base_domain = urlparse(base_url).netloc
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text_lc = a.get_text().lower().strip()
        href_lc = href.lower()
        if any(kw in href_lc or kw in text_lc for kw in _CONTACT_HINTS):
            full = urljoin(base_url, href)
            if urlparse(full).netloc == base_domain and full != base_url:
                return full
    return None


# ── Service ───────────────────────────────────────────────────────────────────

class ScraperService:
    """Synchrone scraper; aanroepen via asyncio.run_in_executor vanuit async context."""

    def scrape(self, url: str) -> Dict:
        result: Dict = {
            'phone': '', 'email': '', 'address': '', 'city': '',
            'postal_code': '', 'kvk_number': '', 'page_text': '',
            'address_raw': '',
        }
        if not url or not url.startswith('http'):
            return result

        try:
            with httpx.Client(
                headers=HEADERS, timeout=12.0,
                follow_redirects=True, verify=False,
            ) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return result

                soup = BeautifulSoup(resp.text, 'lxml')
                _extract_from_html(resp.text, result)

                # Bezoek contact-pagina als primaire data ontbreekt
                needs_more = not result['phone'] or not result['email'] or not result['address']
                if needs_more:
                    contact_url = _find_contact_url(soup, url)
                    if contact_url:
                        try:
                            cr = client.get(contact_url)
                            if cr.status_code == 200:
                                _extract_from_html(cr.text, result)
                        except Exception:
                            pass

        except Exception as e:
            log.debug('[scraper] %s: %s', url, e)

        return result
