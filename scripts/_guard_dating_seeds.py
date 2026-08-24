"""Plaats een hard-stop guard in de verouderde DatingAssistent 40+/50+ seed-scripts.

19-08-2026: datingassistent.nl is de ENIGE DatingAssistent-site. De seeds maakten
spooksites (site_id dating40/dating50) zonder publish-config, waardoor artikelen
faalden met "Geen DATINGASSISTENT50_PUBLISH_URL/_PUBLISH_KEY".
"""
GUARD = (
    'import sys\n'
    'print("GEDEACTIVEERD: geen aparte 40+/50+ DatingAssistent-sites meer. '
    "Gebruik site_id 'datingassistent' (datingassistent.nl).\")\n"
    'sys.exit(1)\n'
)

for f in ("scripts/seed_dating_doelgroepen.py", "scripts/seed_da_week1.py"):
    s = open(f, encoding="utf-8").read()
    if "GEDEACTIVEERD: geen aparte" in s:
        print(f, "al gedaan")
        continue
    i = s.index("import sqlite3")
    open(f, "w", encoding="utf-8").write(s[:i] + GUARD + s[i:])
    print(f, "guard toegevoegd")
