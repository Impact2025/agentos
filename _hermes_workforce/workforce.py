#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified CLI voor het Agent Workforce System.

Eén commando voor alles — je hoeft de losse scripts niet te kennen:
  python3 workforce.py add "tekst" [--project IctusGo] [--tag strategy]
  python3 workforce.py digest [--days 14] [--project IctusGo]
  python3 workforce.py toby            # run watchdog + self-heal
  python3 workforce.py status          # korte gezondheids-samenvatting
  python3 workforce.py bootstrap --interview x.json --out workforce.yaml

Deelt de scripts ai_diary.py / toby.py / bootstrap.py uit dezelfde map.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ai_diary
import toby

def cmd_toby(args):
    toby.main()

def cmd_status(args):
    report, sev = toby.build_report()
    print(f"[{sev}] {report['timestamp']}")
    for f in report["findings"]:
        print(f"  [{f['level']}] {f['msg']}")

def cmd_bootstrap(args):
    import bootstrap
    import json
    a = bootstrap.load_answers(args.interview)
    spec = bootstrap.generate(a)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(spec)
        print(f"[OK] workforce-spec geschreven: {args.out}")
    else:
        print(spec)

def main():
    ap = argparse.ArgumentParser(description="Agent Workforce System CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add"); a.add_argument("text")
    a.add_argument("--project", default=ai_diary.DEFAULT_PROJECT)
    a.add_argument("--tag", choices=sorted(ai_diary.VALID_TAGS))
    a.set_defaults(func=lambda x: ai_diary.add_note(x.text, x.project, x.tag))

    d = sub.add_parser("digest"); d.add_argument("--days", type=int, default=14)
    d.add_argument("--project", default=None)
    d.set_defaults(func=lambda x: ai_diary.digest(x.days, x.project))

    l = sub.add_parser("last"); l.add_argument("--days", type=int, default=7)
    l.add_argument("--project", default=None)
    l.set_defaults(func=lambda x: ai_diary.last(x.days, x.project))

    t = sub.add_parser("toby")
    t.set_defaults(func=cmd_toby)

    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)

    b = sub.add_parser("bootstrap")
    b.add_argument("--interview", required=True)
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_bootstrap)

    args = ap.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
