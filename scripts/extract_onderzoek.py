#!/usr/bin/env python3
"""Extraheer ALLE tekst uit de onderzoeken-PDF's naar een onderzoek-bestand
voor de Teambuilding met Impact blogs. Output: onderzoek-compact.md (per PDF
samenvatting + harde cijfers)."""
import glob, os, re, json
from pypdf import PdfReader

BASE = "D:/APPS/Hermes Brein/Hermes Breind/10_Projects/teambuildingmetimpact"
OUT = os.path.join(BASE, "onderzoek-compact.md")

def extract(pdf):
    r = PdfReader(pdf)
    txt = ""
    for p in r.pages:
        txt += (p.extract_text() or "") + "\n"
    return txt

def compact(name, txt):
    # verwijder overdreven witruimte
    txt = re.sub(r"\s+", " ", txt).strip()
    return f"## {name}\n\n{txt[:3500]}\n"

parts = ["# Onderzoek-compact — Teambuilding met Impact\n",
         "_Gegenereerd uit de onderzoeken-map. Bron voor blogs volgens de redactie-&SEO-gids._\n"]
for f in sorted(glob.glob(BASE + "/onderzoeken/*.pdf")):
    name = os.path.basename(f).replace(".pdf", "")
    try:
        parts.append(compact(name, extract(f)))
    except Exception as e:
        parts.append(f"## {name}\n\n[extractiefout: {e}]\n")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(parts))
print("geschreven:", OUT, "|", os.path.getsize(OUT), "bytes")
