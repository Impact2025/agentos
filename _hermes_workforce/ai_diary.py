#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Diary — context-engine voor de agent-workforce (Allie K. Miller-patroon).

Per-project context-flow: elke entry draagt een --project tag (default "general").
`digest` rollet op tot AI_DIARY_DIGEST.md met een per-project sectie, zodat een
workforce (bijv. IctusGo) alleen z'n eigen context inlaadt.

Stdlib-only — draait overal, geen pip nodig.

Subcommands:
  add "tekst" [--project IctusGo] [--tag focus|strategy|meeting|idea|blocker|win]
  digest [--days 14] [--project IctusGo]   # heel, of gefilterd op 1 project
  last [--days 7] [--project IctusGo]
"""
import argparse
import datetime
import glob
import os
import re
import sys

VAULT = r"D:/APPS/Hermes Brein/Hermes Breind/10_Projects"
DIARY_DIR = os.path.join(VAULT, "_ai_diary")
DIGEST = os.path.join(DIARY_DIR, "AI_DIARY_DIGEST.md")
VALID_TAGS = {"focus", "strategy", "meeting", "idea", "blocker", "win"}
DEFAULT_PROJECT = "general"

def ensure():
    os.makedirs(DIARY_DIR, exist_ok=True)

def _today_file():
    return os.path.join(DIARY_DIR, datetime.date.today().isoformat() + ".md")

def add_note(text, project=None, tag=None):
    ensure()
    project = (project or DEFAULT_PROJECT).strip()
    now = datetime.datetime.now().strftime("%H:%M")
    line = f"- [{now}] #{project}" + (f" #{tag}" if tag else "") + f" {text}\n"
    path = _today_file()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# AI Diary — {datetime.date.today().isoformat()}\n\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    print(f"[OK] toegevoegd aan {os.path.basename(path)} :: [{project}] {text[:70]}")

def transcribe(path):
    if path.lower().endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read().strip()
        if txt:
            add_note(txt, tag="meeting")
            return
    print("[transcribe] geen STT-backend; gebruik een .txt-transcript of add \"...\"")
    sys.exit(2)

def _entry_files(days):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return sorted(
        p for p in glob.glob(os.path.join(DIARY_DIR, "20??-??-??.md"))
        if os.path.basename(p)[:-3] >= cutoff
    )

def _parse_entries(files):
    """Return list of dicts: {date, time, project, tag, text}."""
    out = []
    for p in files:
        date = os.path.basename(p)[:-3]
        with open(p, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"- \[(\d{2}:\d{2})\]\s*(#(\S+))?\s*(#(\S+))?\s*(.*)", line)
                if not m:
                    continue
                time, _, proj, _, tag, text = m.groups()
                out.append({
                    "date": date, "time": time,
                    "project": (proj or DEFAULT_PROJECT),
                    "tag": tag, "text": text.strip(),
                })
    return out

def last(days=7, project=None):
    entries = _parse_entries(_entry_files(days))
    if project:
        entries = [e for e in entries if e["project"].lower() == project.lower()]
    if not entries:
        print("(geen entries)")
        return
    for e in entries:
        print(f"[{e['date']} {e['time']}] #{e['project']}" + (f" #{e['tag']}" if e['tag'] else "") + f" {e['text']}")

def digest(days=14, project=None):
    ensure()
    entries = _parse_entries(_entry_files(days))
    if project:
        entries = [e for e in entries if e["project"].lower() == project.lower()]
    if not entries:
        print("(niets om te rollen)")
        return

    # gefilterde view → aparte per-project file, volledige digest blijft intact
    out_path = os.path.join(DIARY_DIR, f"AI_DIARY_DIGEST_{project}.md") if project else DIGEST

    # groepeer per project, nieuwste eerst
    by_proj = {}
    for e in entries:
        by_proj.setdefault(e["project"], []).append(e)
    for k in by_proj:
        by_proj[k].sort(key=lambda x: (x["date"], x["time"]), reverse=True)

    tag_tally = {}
    for e in entries:
        if e["tag"]:
            tag_tally[e["tag"]] = tag_tally.get(e["tag"], 0) + 1
    tally = ", ".join(f"#{k}×{v}" for k, v in sorted(tag_tally.items(), key=lambda x: -x[1])) or "(geen tags)"

    if project:
        # alleen de gevraagde project-sectie
        items = by_proj.get(project, [])
        lines = [f"# AI Diary Digest — {project} (laatste {days}d)\n",
                 f"- Gegenereerd: {datetime.datetime.now().isoformat(timespec='seconds')}\n",
                 f"- Entries: {len(items)}  |  Tags: {tally}\n\n",
                 f"> Per-project context-view voor de {project}-workforce.\n\n---\n"]
        lines.append(f"## 📁 {project} ({len(items)} entries)\n")
        for e in items:
            lines.append(f"- [{e['time']}]" + (f" #{e['tag']}" if e['tag'] else "") + f" {e['text']}")
        rollup = "\n".join(lines)
    else:
        blocks = [f"# AI Diary Digest (auto-rollup, laatste {days}d)\n"]
        blocks.append(
            f"- Gegenereerd: {datetime.datetime.now().isoformat(timespec='seconds')}\n"
            f"- Entries: {len(entries)}  |  Projecten: {len(by_proj)}\n"
            f"- Tags: {tally}\n\n"
            f"> Rolled-up context voor de agent-workforce (Simon / Toby / domain-agents).\n"
            f"> Per-project secties → laad alleen de sectie van jouw workforce.\n"
            f"> Vers → lees altijd deze file vóór strategische acties.\n\n---\n"
        )
        for proj in sorted(by_proj, key=lambda p: -len(by_proj[p])):
            items = by_proj[proj]
            lines = [f"## 📁 {proj} ({len(items)} entries)\n"]
            for e in items:
                lines.append(f"- [{e['time']}]" + (f" #{e['tag']}" if e['tag'] else "") + f" {e['text']}")
            blocks.append("\n".join(lines) + "\n")
        rollup = "\n".join(blocks)
        words = rollup.split()
        if len(words) > 6000:
            rollup = " ".join(words[-6000:])

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rollup + "\n")
    print(f"[OK] digest geschreven: {out_path}")
    print(f"     entries={len(entries)}  projecten={len(by_proj)}  tags={tally}")

def main():
    ap = argparse.ArgumentParser(description="AI Diary context-engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("text")
    a.add_argument("--project", default=DEFAULT_PROJECT)
    a.add_argument("--tag", choices=sorted(VALID_TAGS))
    t = sub.add_parser("transcribe"); t.add_argument("file")
    d = sub.add_parser("digest"); d.add_argument("--days", type=int, default=14); d.add_argument("--project", default=None)
    l = sub.add_parser("last"); l.add_argument("--days", type=int, default=7); l.add_argument("--project", default=None)
    args = ap.parse_args()
    if args.cmd == "add":
        add_note(args.text, args.project, args.tag)
    elif args.cmd == "transcribe":
        transcribe(args.file)
    elif args.cmd == "digest":
        digest(args.days, args.project)
    elif args.cmd == "last":
        last(args.days, args.project)

if __name__ == "__main__":
    main()
