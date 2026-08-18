"""Wereldklasse-content-motor voor AgentOS (HTTP-API versie).

Escaleert content_jobs die onder de kwaliteitsgrens blijven hangen naar de
Gauntlet Loop, zodat er GEEN content beneden wereldklasse blijft liggen.

BELANGRIJK — waarom dit script niets meer zelf doet
---------------------------------------------------
Tot 15 aug 2026 bouwde dit script zijn eigen objective en POST'te het
rechtstreeks naar `/api/gauntlet`. Daarmee liep het langs twee remmen heen die
in `orchestrator.process_one_under_threshold` zitten:

  * de cross-run cap (`ORCHESTRATOR_MAX_ATTEMPTS`) die een artikel na N zware
    pogingen met rust laat, en
  * `content_pipeline.mark_superseded`, die het bronrecord afsluit zodra de
    herschrijving in de Wachtrij staat.

Erger nog: het schreef na elke escalatie letterlijk
`UPDATE content_jobs SET status='stuck', orchestrator_attempts=1`. Dat zette de
pogingenteller elke ronde terug op 1 en de status terug op 'stuck' — precies de
twee velden waarop de remmen besluiten. Het bronrecord was na elke run dus weer
exact zoals ervoor: onder de grens, 'stuck', teller 1. Resultaat (gemeten
15 aug 2026): één WeAreImpact-artikel 17x herschreven, 128 bijna-identieke
duplicaten in de Wachtrij op twee dagen, en 6,2M tokens op één dag — genoeg om
het dagbudget van 5M te breken en álle andere autonome runs stil te leggen.

De les is niet "het script beter maken" maar "er is maar één weg naar de
Gauntlet". Dit script kiest niet meer zélf, telt niet meer zélf en schrijft niet
meer in `content_jobs`; het roept per ronde `POST /api/orchestrator/process-one`
aan, dat de cap, de dedupe en `mark_superseded` in één beweging doet.

Draait in de AgentOS venv. Gebruikt de logged-in sessie-cookie voor de API.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"D:\APPS\agentos")

COOKIE = r"D:\APPS\agentos\scripts\.agentos_cookie.txt"  # geschreven bij login
API = "http://127.0.0.1:1250"
THRESHOLD = 85
# Hoeveel stukken per aanroep. `process-one` doet er bewust één per call (één
# zware Gauntlet-ronde kost minuten en flink wat tokens); dit script herhaalt
# tot de orchestrator zegt dat er niets meer te doen is.
DEFAULT_MAX_ROUNDS = 5


def _load_cookie():
    """Lees de agentos_session-waarde uit het Netscape cookie-bestand van curl."""
    try:
        with open(COOKIE, encoding="utf-8") as f:
            for line in f:
                m = re.search(r"agentos_session\t(\S+)", line)
                if m:
                    return f"agentos_session={m.group(1)}"
    except FileNotFoundError:
        return ""
    return ""


def _post(path, payload, headers):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode(),
        headers=headers, method="POST")
    # Een Gauntlet-ronde duurt minuten; de orchestrator wacht hem uit.
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode())


def _get(path, headers):
    req = urllib.request.Request(f"{API}{path}", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threshold", type=int, default=THRESHOLD)
    ap.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                    help="maximaal aantal Gauntlet-rondes per aanroep")
    ap.add_argument("--dry-run", action="store_true",
                    help="toon alleen wat er onder de grens staat, escaleer niets")
    args = ap.parse_args()

    cookie = _load_cookie()
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie

    try:
        overzicht = _get(f"/api/orchestrator/under-threshold?threshold={args.threshold}",
                         headers)
    except urllib.error.HTTPError as e:
        print(f"FAIL: kon de lijst niet ophalen — HTTP {e.code} {e.read().decode()[:160]}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: kon de lijst niet ophalen — {e}")
        return 1

    jobs = overzicht.get("jobs", [])
    if not jobs:
        print("Wereldklasse-motor: niets onder de grens. Alles groen.")
        return 0

    print(f"Onder de grens ({args.threshold}): {len(jobs)} stuk(ken)")
    for j in jobs:
        print(f"  - [{j.get('status')}] {j.get('project')}/{(j.get('title') or '')[:60]} "
              f"score={j.get('seo_score')} pogingen={j.get('orchestrator_attempts')}")
    if args.dry_run:
        print("(dry-run — niets geëscaleerd)")
        return 0

    verwerkt = 0
    for ronde in range(1, args.max_rounds + 1):
        try:
            res = _post("/api/orchestrator/process-one",
                        {"threshold": args.threshold}, headers)
        except urllib.error.HTTPError as e:
            print(f"FAIL ronde {ronde}: HTTP {e.code} {e.read().decode()[:160]}")
            break
        except Exception as e:  # noqa: BLE001
            print(f"FAIL ronde {ronde}: {e}")
            break

        if not res.get("processed"):
            # Dit is géén fout: de orchestrator zegt dat er niets (meer) te doen
            # is, of dat het resterende stuk zijn pogingen op heeft. Doorgaan
            # zou precies de storm zijn die dit script veroorzaakte.
            print(f"Klaar na {ronde - 1} ronde(s): {res.get('reason')}")
            break

        verwerkt += 1
        print(f"ronde {ronde}: '{res.get('job_id')}' -> Gauntlet {res.get('run_id')} "
              f"({res.get('run_status')}) -> Wachtrij {res.get('published_job_id')}")

    print(f"Wereldklasse-motor: {verwerkt} artikel(en) herschreven.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
