"""Controleer of elke job met status 'published' ook echt live staat.

Waarom dit bestaat (27 juli 2026): bij het weekrapport bleek dat van de 39
publicaties met een URL er 2 een harde 404 gaven en dat er daarnaast oudere
'published' jobs waren die nooit gerenderd hebben — waaronder één met een slug
vol '+' en haakjes. De status in de database zei 'published'; de site zei iets
anders. Zulke rijen tellen mee in elk voorraadcijfer en in de dedupe (een
zoekwoord blijft bezet door een pagina die niet bestaat), dus een verkeerde
status is niet cosmetisch.

De controle hergebruikt content_pipeline._verify_live, dezelfde functie die het
publicatiepad gebruikt: HTTP 200 is niet genoeg, want een single-page app geeft
voor élke onbekende route dezelfde schil met status 200. Een onbeslisbare
controle (netwerkfout) verklaart een publicatie nooit ten onrechte mislukt.

Gebruik:
    .venv/Scripts/python.exe -m scripts.audit_published_live           # alleen rapporteren
    .venv/Scripts/python.exe -m scripts.audit_published_live --fix     # status corrigeren
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Artikeltitels bevatten emoji en en-dashes; de Windows-console staat standaard
# op cp1252 en laat het script daar hard op crashen — na de controles, dus je
# verliest precies het rapport waar je voor kwam.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - niet-tty
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domains.publish import content_pipeline as cp  # noqa: E402
from backend.shared.database import get_conn  # noqa: E402
from backend.shared.outcomes import log_outcome  # noqa: E402


async def _bereikbaar(url: str) -> tuple[bool, str]:
    """Kunnen we deze URL überhaupt ophalen? (bereikbaar, reden-als-niet)

    describe_exception in plaats van str(e): een httpx.ConnectError geeft een
    lege tekst, en "Live-controle onbeslist voor …: " zonder oorzaak is precies
    het soort melding waar niemand iets mee kan.
    """
    import httpx

    from backend.shared.failures import describe_exception

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            await client.get(url, headers={"User-Agent": "AgentOS-publish-check"})
        return True, ""
    except Exception as e:
        return False, describe_exception(e)


def _job_url(job: dict, base_url: str) -> str:
    """De URL waarop dit artikel hoort te staan.

    Eerst wat het publicatieresultaat zelf zegt — dat is wat er destijds
    daadwerkelijk is aangeroepen. Pas daarna een afleiding uit base_url + slug,
    want die gokt de padstructuur.
    """
    try:
        result = json.loads(job.get("publish_result") or "{}")
    except (ValueError, TypeError):
        result = {}
    for key in ("site", "netlify"):
        section = result.get(key)
        if isinstance(section, dict) and section.get("url"):
            return str(section["url"])
    if result.get("url"):
        return str(result["url"])
    slug = (job.get("slug") or "").strip()
    if base_url and slug:
        return f"{base_url.rstrip('/')}/blog/{slug}"
    return ""


async def audit(fix: bool = False) -> int:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT j.id, j.title, j.slug, j.keyword, j.site_id, j.publish_result, "
            "       j.reviewed_at, s.name AS site_name, s.base_url, "
            "       s.manual_publish "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status = 'published' ORDER BY j.reviewed_at"
        )]

    dood: list[tuple[dict, str]] = []
    geen_url: list[dict] = []
    onbeslist: list[dict] = []
    handmatig: list[dict] = []
    for job in rows:
        # Een manual_publish-site (bv. Liefde voor Iedereen) heeft per definitie
        # géén live URL: het artikel wordt naar de vault geëxporteerd en Vincent
        # plakt het zelf in de site-admin. Een afgeleide /blog/<slug>-URL geeft
        # daar altijd een 404, en die als "niet live" tellen is een verkeerde
        # beschuldiging — het zegt alleen dat de export nog niet overgezet is.
        if job.get("manual_publish"):
            handmatig.append(job)
            continue
        url = _job_url(job, job.get("base_url") or "")
        if not url:
            geen_url.append(job)
            continue
        # _verify_live geeft None voor zowel "staat live" als "kon het niet
        # vaststellen" — voor het publicatiepad is dat juist (een netwerkfout mag
        # een geslaagde publicatie niet afkeuren), maar een audit die beide als
        # 'live' optelt geeft een te rooskleurig cijfer. Daarom hier zelf de
        # bereikbaarheid toetsen en 'onbeslist' apart houden.
        bereikbaar, netwerkfout = await _bereikbaar(url)
        if not bereikbaar:
            onbeslist.append(job)
            print(f"?     {url}  ({netwerkfout})")
            continue
        reden = await cp._verify_live(url)
        status = "DOOD" if reden else "live"
        print(f"{status:5} {url}")
        if reden:
            dood.append((job, reden))

    print(f"\n{len(rows)} gepubliceerd · {len(dood)} niet live · "
          f"{len(onbeslist)} onbeslist · {len(handmatig)} handmatige site · "
          f"{len(geen_url)} zonder URL (niet te controleren)")

    if not dood:
        return 0
    print("\nNiet live:")
    for job, reden in dood:
        print(f"  - [{job['site_name']}] {job['title'][:60]}")
        print(f"    {reden}")

    if not fix:
        print("\n(alleen gerapporteerd — draai met --fix om de status te corrigeren)")
        return len(dood)

    for job, reden in dood:
        with get_conn() as conn:
            conn.execute(
                "UPDATE content_jobs SET status='publish_failed', error=? WHERE id=?",
                (reden, job["id"]),
            )
        # De kaart is het punt: een gecorrigeerde status alleen verplaatst het
        # probleem naar een tabel die niemand leest. status='error' zet hem in
        # het Actiecentrum, met de stap die een mens moet zetten.
        log_outcome(
            project=job["site_name"] or "Publicatie",
            action="publicatie_niet_live",
            detail=(f"'{job['title'][:80]}' stond op 'published' maar is niet "
                    f"live: {reden}"),
            next_step=("Publiceer opnieuw via het Actiecentrum, of verwijder de "
                       "rij als het onderwerp vervallen is."),
            status="error",
        )
    print(f"\n{len(dood)} jobs op 'publish_failed' gezet + uitkomstkaart gelogd.")
    return len(dood)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true",
                        help="corrigeer de status i.p.v. alleen rapporteren")
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(audit(args.fix)) == 0 else 1)
