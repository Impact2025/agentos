"""Bijeen content-fan-out: schrijf de 6 nieuwe GSC-kansen via de 5-fasen SEO-pipeline.
Draait in de ImpactOS venv. Logt elke job naar stdout (job_id, slug, woorden, score, vault-pad).
"""
import sys, json, time, sqlite3
sys.path.insert(0, r"D:\APPS\impactos")

from backend.domains.projects.weareimpact import write_and_publish_status, _run_write_and_publish_job
import backend.domains.projects.weareimpact as w
import asyncio, uuid

# 6 nieuwe kansen (query, angle, rationale)
KANSEN = [
    ("WMO rapportage evenement software",
     "Hoe welzijnsorganisaties impact meten voor WMO met software",
     "Specifieke compliance-vraag; niche expertise wint van generieke alternatieven."),
    ("deelnemersbeheer buurtfestival gratis",
     "Aanmelden en registreren voor kleine buurtinitiatieven",
     "Zeer lokaal, budgetgericht segment dat grote eventtools negeren; lage concurrentie."),
    ("vrijwilligersdag organiseren checklist",
     "Stap-voor-stap planning inclusief deelnemersregistratie",
     "HR/welzijn zoekt concrete handvaten; Bijeen als specialist."),
    ("buurtinitiatieven evenementen meten impact",
     "Waarom tracking van aanwezigheid verbinding sterker maakt",
     "Niche doelgroep met accountability-pijn; mainstream platforms negeren deze vraag."),
    ("gemeente evenement aanmelden systeem",
     "Digitale checkin voor buurtbijeenkomsten en activiteiten",
     "Gemeentes zoeken praktische tools; Bijeen is purpose-built voor de sector."),
    ("Alle stappen om een geslaagd evenement te organiseren",
     "Welzijnsevenement: van mindful binnenkomst tot nazorg en veilige sfeer",
     "Radarsignaal weezevent; welzijn-invalshoek verder dan standaard checklist."),
]


async def run_one(idx, query, angle, rationale):
    site = w._resolve_site("Bijeen")
    job_id = str(uuid.uuid4())
    w._ARTICLE_JOBS[job_id] = {"status": "running", "phase": "start", "percent": 5, "result": None, "error": None}
    task = asyncio.create_task(_run_write_and_publish_job(job_id, "Bijeen", site,
        type("B", (), {"title": angle, "rationale": rationale, "keyword": query})()))
    await task
    # poll status tot done/error
    for _ in range(120):
        st = w._ARTICLE_JOBS.get(job_id, {})
        if st.get("status") in ("done", "error"):
            break
        await asyncio.sleep(2)
    res = w._ARTICLE_JOBS.get(job_id, {})
    r = res.get("result") or {}
    print(f"[{idx}] {query} -> status={res.get('status')} score={r.get('seo_score')} "
          f"words={r.get('word_count')} path={r.get('local_path')}", flush=True)
    return job_id


async def main():
    t0 = time.time()
    for i, (q, a, rat) in enumerate(KANSEN, 1):
        print(f"=== [{i}/6] start: {q} ===", flush=True)
        try:
            await run_one(i, q, a, rat)
        except Exception as e:
            print(f"[{i}] ERROR {q}: {e}", flush=True)
        await asyncio.sleep(1)
    print(f"FANOUT KLAAR in {int(time.time()-t0)}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
