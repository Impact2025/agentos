#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent-Workforce Bootstrap — interview -> workforce-spec (Allie K. Miller).

Laat een agent (of Vincent zelf) het interview invullen, sla op als JSON, en
genereer een op maat gemaakte workforce-specificatie (orchestrator + domain-
directors + leaf-agents + watchdog + context-engine) voor één project.

Gebruik:
  python bootstrap.py --interview antwoorden.json
  python bootstrap.py --interview antwoorden.json --out workforce-mijnproject.yaml

Interview-velden (zie skill 'agent-workforce-bootstrap' voor de vragen):
  project, doel, bottlenecks, kanalen, huidige_tools,
  agent_rollen (lijst), proactiviteit_niveau (1-5), budget_tier (cheap/smart)
"""
import argparse
import datetime
import json
import os

TIER_MODEL = {
    "cheap": "deepseek-v4-flash / qwen3.6-flash (lokale Ollama fallback)",
    "smart": "claude-haiku-4-5 / openmodel smart-tier",
}

def load_answers(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def generate(a):
    project = a.get("project", "Onbekend project")
    rollen = a.get("agent_rollen") or ["Chief of Staff (Simon)", "Domain Director", "Uitvoerder"]
    niveau = int(a.get("proactiviteit_niveau", 3))
    tier = a.get("budget_tier", "cheap")
    today = datetime.date.today().isoformat()
    if niveau <= 2:
        niveau_txt = "reactief tot gedefinieerde triggers"
    elif niveau <= 4:
        niveau_txt = "proactief, maar binnen kaders"
    else:
        niveau_txt = "volledig proactief — alleen bij verified low-risk"
    model = TIER_MODEL.get(tier, TIER_MODEL["cheap"])
    spec = f"""# Agent-Workforce Spec — {project}
# Gegenereerd: {today}  (bootstrap.py)
# Proactiviteit-niveau: {niveau}/5   Budget-tier: {tier}

workforce:
  name: "{project} Agent Workforce"
  orchestrator:
    name: "Simon"
    role: "Chief of Staff — routeert taken, escaleert naar mens bij kritieke beslissing"
    model: "{model}"
    escalation: "mens (Vincent) bij externe writes: GitHub push, e-mail, Stripe, publish"
  context_engine:
    name: "AI Diary"
    source: "D:/APPS/Hermes Brein/Hermes Breind/10_Projects/_ai_diary/AI_DIARY_DIGEST.md"
    cadence: "dagelijks rollup van spraak/notes"
  watchdog:
    name: "Toby"
    role: "Workforce Watchdog — monitort infra, signaleert frictie vóóraf"
    script: "D:/APPS/impactos/_hermes_workforce/toby.py"
  directors:
"""
    for r in rollen:
        spec += f"""    - name: "{r}"
      model: "{model}"
      proactivity: {niveau}
      reports_to: "Simon"
"""
    spec += f"""
  guardrails:
    - "Geen externe write zonder orchestrator-go/no-go"
    - "Proactiviteit-niveau {niveau}: {niveau_txt}"
    - "Context eerst: laad AI_DIARY_DIGEST vóór strategische actie"

  kanalen: {json.dumps(a.get('kanalen', []), ensure_ascii=False)}
  bottlenecks: {json.dumps(a.get('bottlenecks', []), ensure_ascii=False)}
  doel: {json.dumps(a.get('doel', ''), ensure_ascii=False)}
"""
    return spec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interview", required=True, help="pad naar antwoorden.json")
    ap.add_argument("--out", help="output .yaml/.md pad")
    args = ap.parse_args()
    a = load_answers(args.interview)
    spec = generate(a)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(spec)
        print(f"[OK] workforce-spec geschreven: {args.out}")
    else:
        print(spec)

if __name__ == "__main__":
    main()
