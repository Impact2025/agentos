"""Verificatie van de SERP-Omni engine zonder de live server te raken.

Start een TestClient tegen een wegwerp-DB (IMPACTOS_DB_PATH override) op een
eigen poort. We monkeypatchen `websearch.search` zodat de test niet afhankelijk
is van Tavily/Brave/DDG-quota, en `article_writer._llm` zodat er geen echt LLM-
verkeer nodig is. We verifiëren de volledige chain:

  POST /api/omni/analyze   -> assets in omni_queue (staged)
  GET  /api/omni/queue     -> staged assets zichtbaar
  POST /api/omni/queue/{id}/approve  -> status 'approved'
  POST /api/omni/queue/{id}/publish  -> LinkedIn niet geconfigureerd -> blijft approved, hint
"""
import os, sys, json, tempfile, sqlite3

# Zet de repo-root op het pad zodat `backend` als package importeerbaar is
# (backend-package-dir = D:/APPS/impactos/backend, dus parent moet op het pad).
sys.path.insert(0, "D:/APPS/impactos")

# Wegwerp-DB zodat de live data ongemoeid blijft.
_TMP = os.path.join(tempfile.gettempdir(), "agentos_omni_test.db")
if os.path.exists(_TMP):
    os.remove(_TMP)
os.environ["IMPACTOS_DB_PATH"] = _TMP

# Voorkom dat de echte .env de LLM-route dwingt; we patchen toch.
import backend.shared.config as cfg  # noqa

# Monkeypatch websearch.search vóór import van omni.
import backend.shared.websearch as ws

def fake_search(query, max_results=12, exclude_domains=None):
    # Simuleer een SERP waarin Reddit + YouTube domineren (hoogste AIO-hefboom).
    return [
        {"title": "Beste tips volgens de community", "url": "https://www.reddit.com/r/example/thread/1", "snippet": "reddit discusses the topic"},
        {"title": "Uitleg video", "url": "https://www.youtube.com/watch?v=abc", "snippet": "video tutorial"},
        {"title": "LinkedIn inzicht", "url": "https://www.linkedin.com/posts/x", "snippet": "professional take"},
        {"title": "Een blog", "url": "https://www.someblog.com/post", "snippet": "generic article"},
    ]

ws.search = fake_search

# Monkeypatch article_writer._llm zodat er geen echt LLM-verkeer nodig is.
import backend.domains.publish.article_writer as aw

def fake_llm(system, prompt, max_tokens=2000):
    if "reddit_post" in prompt:
        return json.dumps({"title": "Eerlijke vraag over het onderwerp", "body": "Ik ben benieuwd hoe anderen dit aanpakken. Deel je ervaring?"})
    if "youtube_script" in prompt:
        return json.dumps({"title": "Uitleg in 3 minuten", "hook": "Dit is waarom het werkt", "outline": ["stap 1", "stap 2"], "description": "Korte uitleg over het onderwerp"})
    if "linkedin_article" in prompt:
        return json.dumps({"title": "Mijn professionele kijk", "body": "Een scherpe observatie met een vraag aan het eind. Wat denk jij?"})
    if "x_post" in prompt:
        return json.dumps({"title": "", "body": "Korte scherpe gedachte over het onderwerp."})
    if "aeo_snippet" in prompt:
        return json.dumps({"title": "Antwoord", "direct_answer": "Het korte antwoord is dit in veertig woorden zonder verzonnen cijfers.", "faq": [{"q": "Wat is het?", "a": "Een uitleg."}]})
    return json.dumps({"title": "Titel", "body": "Bodytekst van het asset."})

aw._llm = fake_llm

# Laad de app (init_db draait in lifespan).
from fastapi.testclient import TestClient
import backend.main as appmod
appmod.init_db()  # zorg dat de test-DB de tabellen heeft vóór we rows invoegen

client = TestClient(appmod.app)

# Login cookie (IMPACTOS_PASSWORD staat in .env; testclient gebruikt de auth-guard).
# Zonder wachtwoord staat de guard uit in dev — we proberen zonder login.
PW = os.environ.get("IMPACTOS_PASSWORD", "Test1234")
login = client.post("/api/auth/login", json={"password": PW})
print("login:", login.status_code)
if login.status_code == 200 and "set-cookie" in login.headers:
    client.cookies.set("agentos_session", login.cookies.get("agentos_session", ""))

# Haal een site-id op (gebruik de eerste site in de test-DB; die is leeg -> maak er een).
with sqlite3.connect(_TMP) as c:
    c.execute("INSERT OR IGNORE INTO sites (id, name, base_url, created_at) VALUES ('site_test','TestSite','https://testsite.nl','')")
    sid = "site_test"

print("\n[1] analyze 'hond adoptie'")
r = client.post("/api/omni/analyze", json={"site_id": sid, "keyword": "hond adoptie"})
print("  status:", r.status_code)
j = r.json()
print("  serp.dominant:", j.get("serp", {}).get("dominant"))
print("  recommended_assets:", j.get("serp", {}).get("recommended_assets"))
print("  queued:", j.get("queued"))
assert r.status_code == 200
assert "reddit_post" in j["serp"]["recommended_assets"], "Reddit moet aanbevolen zijn"

print("\n[2] queue")
r = client.get("/api/omni/queue", params={"site_id": sid})
q = r.json()
print("  aantal staged:", len(q))
assert len(q) >= 1

first_id = q[0]["id"]
print("\n[3] approve", first_id)
r = client.post(f"/api/omni/queue/{first_id}/approve")
print("  status:", r.status_code, r.json().get("status"))
assert r.json().get("status") == "approved"

print("\n[4] publish (LinkedIn niet geconfigureerd -> blijft approved, hint)")
r = client.post(f"/api/omni/queue/{first_id}/publish")
print("  status:", r.status_code, r.json())
# Reddit/x_post/publication: LinkedIn is niet geconfigureerd -> published=False, approved
assert r.json().get("published") is False

print("\n[5] BAD: publish zonder approve moet 422")
r2 = client.post(f"/api/omni/queue/{q[1]['id']}/publish")
print("  status:", r2.status_code)
assert r2.status_code == 422

print("\n[6] status endpoint")
r = client.get("/api/omni/status")
print("  ", r.json())

print("\nALLE TESTS GESLAAGD ✅")
