import re
import asyncio
import httpx
from datetime import datetime
from typing import List
from .base import Tool, ToolResult

_RSS_FEEDS = {
    "nos_economie":      ("NOS Economie",       "https://feeds.nos.nl/nosnieuwseconomie"),
    "rtl_economie":      ("RTL Nieuws Economie", "https://www.rtlnieuws.nl/tags/economie.rss"),
    "reuters_business":  ("Reuters Business",    "https://feeds.reuters.com/reuters/businessNews"),
    "investing_nl":      ("Investing.com NL",    "https://nl.investing.com/rss/news.rss"),
}

_HEADERS = {"User-Agent": "Mozilla/5.0 ImpactOS/1.0 financial-reader"}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


class FinancialNewsTool(Tool):
    name = "fetch_financial_news"
    description = (
        "Haal actueel financieel nieuws op uit Nederlandse en internationale bronnen "
        "(NOS Economie, RTL Nieuws, Reuters Business). Filter optioneel op trefwoord "
        "(bijv. 'ASML', 'rente', 'AEX', 'bitcoin'). Gebruik dit altijd voordat je "
        "een financieel advies of analyse geeft."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Optioneel zoekwoord om nieuws te filteren, bijv. 'ASML', 'rente', 'inflatie'.",
                "default": "",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string", "enum": list(_RSS_FEEDS.keys())},
                "description": "Selecteer specifieke bronnen. Leeg = alle bronnen.",
                "default": [],
            },
            "max_items": {
                "type": "integer",
                "description": "Maximaal aantal nieuwsberichten (standaard 8).",
                "default": 8,
            },
        },
        "required": [],
    }

    async def run(self, keyword: str = "", sources: list = None, max_items: int = 8) -> ToolResult:
        sources = sources or []
        selected = {k: v for k, v in _RSS_FEEDS.items() if not sources or k in sources}

        raw_results = await asyncio.gather(
            *[self._fetch_feed(label, url, keyword) for label, url in selected.values()],
            return_exceptions=True,
        )

        articles: List[dict] = []
        for r in raw_results:
            if isinstance(r, list):
                articles.extend(r)

        articles.sort(key=lambda x: x.get("date_sort", ""), reverse=True)
        articles = articles[:max_items]

        if not articles:
            msg = f"Geen financieel nieuws gevonden" + (f" voor '{keyword}'" if keyword else "") + "."
            return ToolResult(self.name, msg)

        header = f"## Financieel Nieuws{' — ' + keyword if keyword else ''}\n"
        parts = [header]
        for a in articles:
            parts.append(f"### {a['title']}")
            parts.append(f"*{a['source']}* · {a.get('date_display', 'datum onbekend')}")
            if a.get("summary"):
                parts.append(a["summary"])
            if a.get("link"):
                parts.append(f"[Lees meer]({a['link']})")
            parts.append("")

        return ToolResult(self.name, "\n".join(parts))

    async def _fetch_feed(self, label: str, url: str, keyword: str) -> list:
        try:
            import feedparser
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=_HEADERS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content = resp.text

            feed = feedparser.parse(content)
            articles = []

            for entry in feed.entries:
                title = _strip_html(entry.get("title", ""))
                summary = _strip_html(entry.get("summary") or entry.get("description", ""))[:350]
                link = entry.get("link", "")

                if keyword:
                    haystack = (title + " " + summary).lower()
                    if keyword.lower() not in haystack:
                        continue

                date_sort = ""
                date_display = "datum onbekend"
                parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if parsed:
                    try:
                        dt = datetime(*parsed[:6])
                        date_sort = dt.isoformat()
                        date_display = dt.strftime("%d-%m-%Y %H:%M")
                    except Exception:
                        pass

                articles.append({
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "source": label,
                    "date_sort": date_sort,
                    "date_display": date_display,
                })

            return articles

        except ImportError:
            return [{"title": "⚠ feedparser niet geïnstalleerd", "summary": "Voeg 'feedparser' toe aan requirements.txt.", "source": label, "link": ""}]
        except Exception:
            return []
