"""Eenmalige schoonmaak Bijeen-wachtrij (17 -> 7).

Reject exact de 10 niet-onderscheidende / duplicaat-artikelen via dezelfde
`reject_job` die de UI-knop "Wijs af" aanroept, zodat de status + activity-log
identiek lopen. None of the 17 are published, so reject_job only flips
status->rejected + logs "afgewezen" (no depublish card).
"""
import os
import sys

ROOT = r"D:/apps/impactos"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from backend.domains.publish import content_pipeline as cp

# 10 te rejecten (zie beslissingstabel). Keep-set = 4,6,10,12,13,14,17.
REJECT = [
    "66d91596-1f73-4d6f-bc48-308b0290d9b7",  # 1 netwerk 7 stappen (86, netwerk-fam)
    "0ce789f9-d472-4155-a0a8-bf0f7321e827",  # 2 sociale cohesie (DUP van live 2x)
    "6aae520b-940e-460a-8f91-458f1ab965be",  # 3 verbinding sociale cohesie (redundant #2)
    "f797bae9-b498-4104-bcfa-45b59eb1408c",  # 5 gemeente aanmelden zonder gedoe (paar)
    "d05bba8b-e5c7-448e-b3b5-1a6dd5bffc95",  # 7 impact zonder formulieren (impact-cluster)
    "3c469f48-c18b-4531-ba8b-9c1c92ee5a50",  # 8 lifecycle (near-dup #13)
    "ace69c18-32c5-498a-ad08-53c3c80d3bc2",  # 9 gemeente systeem (paar)
    "f8a16911-77de-45d5-9fd1-72ea518b7e2f",  # 11 WMO-rapportage (impact-cluster)
    "7fbe86ec-09b5-4210-a9d1-3d55082cf40c",  # 15 impactrapportage 3 stappen (impact-cluster)
    "ac27991c-61f9-4fbf-8f9b-6e3d92affa3f",  # 16 netwerkbijeenkomst doel-impact (near-dup #12)
]

ok, err = 0, 0
for jid in REJECT:
    try:
        cp.reject_job(jid)
        ok += 1
        print("REJECTED", jid)
    except Exception as e:
        err += 1
        print("ERR", jid, repr(e))

print(f"\nDone: {ok} rejected, {err} errors")

# Verificatie: tel resterende pending_review voor Bijeen
import sqlite3
con = sqlite3.connect("data/impactos.db")
cur = con.cursor()
cur.execute(
    "SELECT COUNT(*) FROM content_jobs WHERE site_id=? AND status='pending_review'",
    ("5e12805c-ca0d-4df6-9204-af036ba546e2",),
)
rest = cur.fetchone()[0]
print("Bijeen pending_review resterend:", rest)
cur.execute(
    "SELECT title, seo_score FROM content_jobs WHERE site_id=? AND status='pending_review' ORDER BY seo_score DESC",
    ("5e12805c-ca0d-4df6-9204-af036ba546e2",),
)
print("Overgebleven wachtrij:")
for t, s in cur.fetchall():
    print(f"  SEO {s}  {t}")
