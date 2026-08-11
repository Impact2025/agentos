"""Iris-onboarding: de vier intake-stappen en de per-klant OAuth-terugval.

Elke stap schrijft direct naar de bestaande tabel waar dat veld al hoort
(sites.profile, iris_knowledge, project_autonomy) — deze tests bewijzen dat
de waarde daar ook echt terechtkomt, niet in een losse kopie. De
resolve-tests bewijzen de backward-compat-belofte uit het plan: zonder een
per-site koppeling gedraagt alles zich exact als vóór dit domein bestond.
"""
import json

import pytest


@pytest.fixture()
def onb_site(conn, clean_tables):
    from backend.domains.seo import sites as sites_service
    site = sites_service.create_site({"name": "Onboarding Testklant"})
    yield site
    from backend.shared.database import get_conn
    with get_conn() as c:
        c.execute("DELETE FROM sites WHERE id = ?", (site["id"],))
        c.execute("DELETE FROM iris_knowledge WHERE lower(scope) = lower(?)", (site["name"],))
        c.execute("DELETE FROM project_autonomy WHERE project = ?", (site["name"],))
        c.execute("DELETE FROM oauth_accounts WHERE site_id = ?", (site["id"],))


def _mock_distiller(monkeypatch, scope):
    from backend.domains.iris import service as iris_service

    async def fake_llm(system, prompt, max_tokens=1500):
        return json.dumps({
            "samenvatting": "Informeel, deskundig, korte zinnen.",
            "principes": ["Nooit uitroeptekens", "Altijd afsluiten met een concrete stap"],
            "tags": ["merk"],
            "scope": "all",  # de LLM raadt bewust fout — de test bewijst dat scope=project wint
        })
    monkeypatch.setattr(iris_service, "_llm", fake_llm)


# ── Stap 1: bedrijfsdoel ────────────────────────────────────────────────────

def test_stap1_schrijft_naar_sites_profile(onb_site):
    from backend.domains.onboarding import service

    status = service.save_step1(onb_site["id"], "Wij helpen coaches aan hun eerste 10 klanten.")
    assert status["steps"]["1_bedrijfsdoel"]["profile"] == "Wij helpen coaches aan hun eerste 10 klanten."

    from backend.domains.seo import sites as sites_service
    site = sites_service.get_site(onb_site["id"])
    assert site["profile"] == "Wij helpen coaches aan hun eerste 10 klanten."


def test_stap1_onbekende_site_geeft_valueerror(onb_site):
    from backend.domains.onboarding import service
    with pytest.raises(ValueError):
        service.save_step1("bestaat-niet", "profiel")


# ── Stap 2: schrijfstijl → iris_knowledge, scope geforceerd op het project ─

@pytest.mark.asyncio
async def test_stap2_forceert_scope_ondanks_llm_gok(onb_site, monkeypatch):
    from backend.domains.onboarding import service
    from backend.domains.iris import knowledge

    _mock_distiller(monkeypatch, scope=onb_site["name"])
    await service.save_step2(onb_site["id"], "Informeel maar deskundig, korte zinnen, geen uitroeptekens.")

    items = knowledge.active_principles(scope_project=onb_site["name"])
    assert any("Schrijfstijl" in it["title"] for it in items)
    # De LLM gokte scope='all' — save_step2 moet dat overschrijven met het echte project.
    matching = [it for it in items if "Schrijfstijl" in it["title"]]
    assert matching and matching[0]["scope"].lower() == onb_site["name"].lower()


@pytest.mark.asyncio
async def test_stap2_weigert_te_kort(onb_site, monkeypatch):
    from backend.domains.onboarding import service
    with pytest.raises(ValueError):
        await service.save_step2(onb_site["id"], "kort")


# ── Stap 4: autonomie → project_autonomy ────────────────────────────────────

def test_stap4_preset_upsert(onb_site):
    from backend.domains.onboarding import service

    status = service.save_step4(onb_site["id"], "laag")
    assert status["steps"]["4_autonomie"]["current"]["content_run_max"] == 1

    # Opnieuw opslaan met een override overschrijft, maakt geen tweede rij.
    status2 = service.save_step4(onb_site["id"], "hoog", overrides={"content_run_max": 9})
    assert status2["steps"]["4_autonomie"]["current"]["content_run_max"] == 9
    assert status2["steps"]["4_autonomie"]["current"]["outreach_max"] == 15  # uit de 'hoog'-preset

    from backend.shared.database import get_conn
    with get_conn() as c:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM project_autonomy WHERE project = ?", (onb_site["name"],),
        ).fetchone()["n"]
    assert n == 1


def test_stap4_onbekende_preset_geeft_valueerror(onb_site):
    from backend.domains.onboarding import service
    with pytest.raises(ValueError):
        service.save_step4(onb_site["id"], "extreem")


# ── Afronden: nooit stil met een half ingevuld profiel ──────────────────────

@pytest.mark.asyncio
async def test_complete_weigert_bij_ontbrekende_stappen(onb_site):
    from backend.domains.onboarding import service
    with pytest.raises(ValueError, match="ontbreekt"):
        service.complete_onboarding(onb_site["id"])


@pytest.mark.asyncio
async def test_complete_zet_onboarded_at_na_alle_stappen(onb_site, monkeypatch):
    from backend.domains.onboarding import service

    service.save_step1(onb_site["id"], "Wij helpen coaches aan hun eerste 10 klanten via LinkedIn.")
    _mock_distiller(monkeypatch, scope=onb_site["name"])
    await service.save_step2(onb_site["id"], "Informeel maar deskundig, korte zinnen.")
    service.save_step4(onb_site["id"], "normaal")

    status = service.complete_onboarding(onb_site["id"])
    assert status["onboarded_at"]


# ── iris/actions.py: per-project autonomie override ─────────────────────────

def test_iris_actions_leest_project_autonomy(onb_site):
    from backend.domains.iris import actions

    assert actions._autonomy_max(onb_site["name"], "content_run_max", 3) == 3  # nog geen rij → fallback

    from backend.domains.onboarding import service
    service.save_step4(onb_site["id"], "laag")
    assert actions._autonomy_max(onb_site["name"], "content_run_max", 3) == 1


# ── resolve.py: terugval zonder per-klant koppeling (geen netwerkcalls) ────

def test_microsoft_token_for_valt_terug_zonder_koppeling(onb_site, monkeypatch):
    from backend.domains.onboarding import resolve
    from backend.domains.outlook import service as outlook_service

    calls = []

    def fake_global_token():
        calls.append("global")
        return "een-globaal-token"
    monkeypatch.setattr(outlook_service, "get_valid_token", fake_global_token)

    # Geen oauth_accounts-rij voor deze site → direct terugval, geen import van
    # oauth_microsoft, geen netwerkcall.
    assert resolve.microsoft_token_for(onb_site["id"]) == "een-globaal-token"
    assert calls == ["global"]


def test_google_credentials_for_geeft_none_zonder_koppeling(onb_site):
    from backend.domains.onboarding import resolve
    assert resolve.google_credentials_for(onb_site["id"]) is None


def test_microsoft_token_for_gebruikt_per_site_account_als_die_bestaat(onb_site, monkeypatch):
    from backend.domains.onboarding import resolve, oauth_microsoft
    from backend.shared.database import get_conn

    now = "2026-08-11T00:00:00+00:00"
    with get_conn() as c:
        c.execute(
            "INSERT INTO oauth_accounts (id, site_id, provider, account_email, "
            "credentials_json, scopes, created_at, updated_at) "
            "VALUES ('x', ?, 'microsoft', 'klant@voorbeeld.nl', '{}', '', ?, ?)",
            (onb_site["id"], now, now),
        )

    monkeypatch.setattr(oauth_microsoft, "get_valid_token_for_site", lambda site_id: "per-klant-token")
    assert resolve.microsoft_token_for(onb_site["id"]) == "per-klant-token"


def test_outlook_get_valid_token_zonder_site_id_ongewijzigd(monkeypatch):
    """Alle bestaande aanroepen (~20 call sites) geven geen site_id door —
    dat pad mag door dit domein niet aangeraakt worden."""
    from backend.domains.outlook import service as outlook_service

    monkeypatch.setattr(outlook_service, "is_configured", lambda: False)
    assert outlook_service.get_valid_token() is None
