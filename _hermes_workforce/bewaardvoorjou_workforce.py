#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BewaardVoorJou — Workforce Orchestrator (Simon).

Stuurt de directors aan zoals Allie K. Miller's Chief of Staff:
  maandag    → Content Director genereert de 3 artikelen van de week
  woensdag   → Publish Director pusht klaar-drafts als DRAFT (pending jouw go/no-go)
  vrijdag    → Social Director maakt per gepubliceerd artikel een campagne

Elke externe write (publish, social POST) stopt bij een escalatie-queue —
jij keurt goed. Dit is de harde guardrail uit de workforce-spec.

Werkwijze:
  python3 bewaardvoorjou_workforce.py week        # toon dit week-plan
  python3 bewaardvoorjou_workforce.py run --day maandag|woensdag|vrijdag
  python3 bewaardvoorjou_workforce.py escalate     # toon openstaande go/no-go's
"""
import argparse, datetime, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUEUE = HERE / "escalation_queue"

def _run(script, *args):
    r = subprocess.run([sys.executable, str(HERE / script), *args],
                       capture_output=True, text=True, timeout=300)
    print(r.stdout)
    if r.returncode != 0 and r.stderr:
        print(r.stderr)

def run_day(day):
    if day == "maandag":
        print("== Maandag: Content Director genereert week-artikelen ==")
        _run("director_content.py", "generate")
    elif day == "woensdag":
        print("== Woensdag: Publish Director pusht drafts (pending go/no-go) ==")
        _run("director_publish.py", "list")
    elif day == "vrijdag":
        print("== Vrijdag: Social Director maakt campagnes ==")
        _run("director_social.py", "list")

def escalate():
    if not QUEUE.exists():
        print("(geen escalaties)")
        return
    items = sorted(QUEUE.glob("*.json"))
    if not items:
        print("(geen escalaties)")
        return
    for f in items:
        item = json.loads(f.read_text(encoding="utf-8"))
        print(f"  [{item['status']}] {item['slug']} / {item['kind']}: {item['msg']}")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("week")
    r = sub.add_parser("run"); r.add_argument("--day", required=True,
        choices=["maandag", "woensdag", "vrijdag"])
    sub.add_parser("escalate")
    args = ap.parse_args()
    if args.cmd == "week":
        print("Week-cadans BewaardVoorJou-workforce:")
        print("  maandag   → Content Director: 3 artikelen genereren")
        print("  woensdag  → Publish Director: drafts pushen (pending jouw go/no-go)")
        print("  vrijdag   → Social Director: campagnes per artikel (pending review)")
    elif args.cmd == "run":
        run_day(args.day)
    elif args.cmd == "escalate":
        escalate()

if __name__ == "__main__":
    main()
