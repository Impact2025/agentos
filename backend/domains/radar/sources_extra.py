"""Astros — extra bronnen zonder API-key.

Het bestaande Radar-domein haalt via Tavily (keyword/competitor) en RSS.
De "Hermes Astros"-video belooft ook "watch creators" — YouTube- en
Reddit-accounts die je volgt. Die bronnen vergen normaal een API-key
(YouTube Data API, Reddit OAuth), maar beide platforms bieden een
key-vrije weg:

  * YouTube: elke channel heeft een verborgen RSS-feed op
    https://www.youtube.com/feeds/videos.xml?channel_id=CHID
    (en via handle: ?user=HANDLE). Geen key, geen quota.
  * Reddit: de publieke JSON van een subreddit/user:
    https://www.reddit.com/r/SUB/new.json  (of /user/NAME/submitted.json)
    met een browser-User-Agent. Geen key, wel een zachte rate-limit.

Deze module is volledig los van Tavily: als Tavily-quota op is, blijven
creators gewoon scannen. Faalt zacht (lege lijst) bij netwerkstoring.

Output-shape per item is identiek aan wat _gather van de Radar verwacht:
  {"title", "url", "source", "snippet", "published_days_ago", "tavily_score"}
zodat de bestaande scorer/verrijking er direct op werkt.
"""
from __future__ import annotations

import json
import re
import time as _time
from datetime import datetime, timezone
from typing import Dict, List

import httpx

log = __import__("logging").getLogger(__name__)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Hoe ver terug we een creator-post als "vers" zien (dagen).
CREATOR_LOOKBACK_DAYS = 14
_HTTP_TIMEOUT = 12.0


def _days_ago(dt_utc: datetime) -> int:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt_utc
    return max(0, delta.days)


def _channel_id_from_handle(handle: str) -> str | None:
    """Resolve een @handle naar een channel_id via de RSS-feed (key-vrij)."""
    handle = handle.lstrip("@").strip()
    if not handle:
        return None
    feed = f"https://www.youtube.com/feeds/videos.xml?user={handle}"
    try:
        r = httpx.get(feed, headers=_UA, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            return None
        m = re.search(r"<yt:channelId>([^<]+)</yt:channelId>", r.text)
        return m.group(1) if m else None
    except Exception as e:  # noqa: BLE001 — zacht falen
        log.warning("[astros] YouTube-handle resolve mislukt voor %s: %s", handle, e)
        return None


def _fetch_youtube(channel_value: str) -> List[Dict]:
    """Haal recente video's van een YouTube-channel (key-vrij via RSS).

    `channel_value` mag zijn: een channel_id (UC...), een @handle, of een
    volledige channel-URL. We proberen eerst de ID, dan de handle.
    """
    raw = channel_value.strip()
    cid = None
    if raw.startswith("UC") and len(raw) > 12:
        cid = raw
    else:
        m = re.search(r"(UC[\w-]{20,})", raw)
        if m:
            cid = m.group(1)
        else:
            handle = raw.split("/")[-1].lstrip("@") if "/" in raw else raw.lstrip("@")
            cid = _channel_id_from_handle(handle)
    # YouTube geeft in de user-feed soms een channelId met een '_'-prefix
    # terug (bijv. '_x5XG1OV2P6uZZ5FSM9Ttw' i.p.v. 'UC_x5XG1OV2P6uZZ5FSM9Ttw').
    # Het echte ID begint met 'UC_'. Als de prefix een '_' is, vervang die
    # ene '_' door 'UC_' (behoud de rest inclusief de tweede underscore).
    if cid and not cid.startswith("UC") and cid.startswith("_"):
        cid = "UC" + cid
    if not cid:
        log.warning("[astros] Geen YouTube channel_id voor '%s'", channel_value)
        return []

    feed = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    try:
        r = httpx.get(feed, headers=_UA, timeout=_HTTP_TIMEOUT, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        log.warning("[astros] YouTube-feed ophalen mislukt: %s", e)
        return []
    if r.status_code != 200:
        log.warning("[astros] YouTube-feed gaf status %s", r.status_code)
        return []

    out: List[Dict] = []
    entries = re.findall(r"<entry>(.*?)</entry>", r.text, re.S)
    for e in entries[:8]:
        title_m = re.search(r"<title>(.*?)</title>", e, re.S)
        vid_m = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", e)
        pub_m = re.search(r"<published>([^<]+)</published>", e)
        desc_m = re.search(r"<media:description>(.*?)</media:description>", e, re.S)
        if not (title_m and vid_m):
            continue
        vid = vid_m.group(1)
        url = f"https://www.youtube.com/watch?v={vid}"
        pub = None
        if pub_m:
            try:
                pub = datetime.fromisoformat(pub_m.group(1).replace("Z", "+00:00"))
            except Exception:
                pub = None
        days = _days_ago(pub) if pub else -1
        if days > CREATOR_LOOKBACK_DAYS:
            continue
        out.append({
            "title": re.sub(r"\s+", " ", title_m.group(1)).strip(),
            "url": url,
            "source": "youtube",
            "snippet": (desc_m.group(1)[:500] if desc_m else "") or "",
            "published_days_ago": days,
            # Creators zijn per definitie hoogwaardige signalen; we geven een
            # stevige Tavily-surrogaat-score zodat ze de verrijkingsdrempel halen.
            "tavily_score": 0.7,
        })
    return out


def _fetch_reddit(value: str) -> List[Dict]:
    """Haal recente posts van een subreddit of Reddit-user (key-vrij).

    `value` mag zijn: 'r/subreddit', 'subreddit', 'u/username' of 'user/name'.

    De publieke JSON-API (`.json`) wordt sinds 2023 actief geblokkeerd met
    403, ook met een nette User-Agent. Het verborgen **RSS-endpoint**
    (`/new.rss`) geeft wél ongeauthenticeerd de laatste posts terug — dat
    gebruiken we. Parsen via regex op <entry>/<title>/<link>/<updated>/<content>.
    """
    raw = value.strip().lower().lstrip("/")
    if raw.startswith("r/"):
        kind, name = "r", raw[2:]
    elif raw.startswith("u/") or raw.startswith("user/"):
        kind, name = "user", raw.split("/", 1)[1]
    else:
        # Veronderstel een subreddit als er geen prefix zit.
        kind, name = "r", raw
    if not name:
        return []
    url = f"https://www.reddit.com/{kind}/{name}/new.rss?limit=15"

    last_err = ""
    for attempt in range(3):  # retry bij rate-limit (429) / tijdelijke storing
        try:
            r = httpx.get(url, headers=_UA, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            log.warning("[astros] Reddit-ophalen mislukt voor %s: %s", value, e)
            continue
        if r.status_code == 429:
            last_err = "429 rate-limit"
            _time.sleep(2.5 * (attempt + 1))  # backoff: 2.5s, 5s
            continue
        if r.status_code != 200:
            last_err = f"status {r.status_code}"
            log.warning("[astros] Reddit gaf status %s voor %s", r.status_code, value)
            break
        break
    else:
        log.warning("[astros] Reddit gaf na retries %s voor %s", last_err, value)
        return []

    if r.status_code != 200:
        return []

    out: List[Dict] = []
    # Reddit-RSS: <entry><title>..</title><link>URL</link><updated>ISO</updated>
    #           <content>HTML</content></entry>
    entries = re.findall(r"<entry>(.*?)</entry>", r.text, re.S)
    for e in entries[:15]:
        t_m = re.search(r"<title>(.*?)</title>", e, re.S)
        l_m = re.search(r"<link>(.*?)</link>", e, re.S)
        u_m = re.search(r"<updated>([^<]+)</updated>", e)
        c_m = re.search(r"<content>(.*?)</content>", e, re.S)
        if not (t_m and l_m):
            continue
        title = re.sub(r"\s+", " ", t_m.group(1)).strip()
        link = l_m.group(1).strip()
        # Reddit link bevat soms &amp; — normaliseer.
        link = link.replace("&amp;", "&")
        pub = None
        if u_m:
            try:
                pub = datetime.fromisoformat(u_m.group(1).replace("Z", "+00:00"))
            except Exception:
                pub = None
        days = _days_ago(pub) if pub else -1
        if days > CREATOR_LOOKBACK_DAYS:
            continue
        # Haal platte tekst uit de HTML-content (verwijder tags).
        content = c_m.group(1) if c_m else ""
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()
        out.append({
            "title": title,
            "url": link,
            "source": "reddit",
            "snippet": content[:400],
            "published_days_ago": days,
            "tavily_score": 0.6,
        })
    return out


def gather_creator(watch_type: str, value: str) -> List[Dict]:
    """Unified entry voor de nieuwe Astros-watchtypes ('youtube' / 'reddit')."""
    if watch_type == "youtube":
        return _fetch_youtube(value)
    if watch_type == "reddit":
        return _fetch_reddit(value)
    return []
