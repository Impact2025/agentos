"""De knop "Analyseer & fix" — wat hij mag beloven en wat niet.

Aanleiding (6 aug 2026): op een audit-kaart over cluster-kannibalisatie koos de
triage-LLM een contentronde — precies de ingreep die die invariant verbiedt
("schrijf er géén nieuw artikel bij"). Er kwam niets uit, en Vincent las
"❌ Geen uitvoering opgeleverd — zie de foutkaart hierboven" onder een keurige
diagnose, met een verwijzing naar een kaart die niet bestaat: dit ís de kaart.

Drie dingen die deze suite vasthoudt:

1. Een `waarheidsaudit`-kaart komt nooit bij de LLM-whitelist terecht. Het
   antwoord staat al in het invariant-register (`stap`) en op de kaart zelf.
2. Een remedie die niets oplevert, zegt dat in gewone taal en wordt geen
   ingesleten aanpak (de rem `_verleer_bij_aanhoudend_falen`).
3. De invariant `triage_remedie_zonder_effect` ziet het als die rem faalt.
"""
import uuid

import pytest

from backend.domains.iris import integrity as ig
from backend.domains.iris import triage
from backend.shared.database import get_conn


def _kaart(action="waarheidsaudit", detail="", next_step="", project="Systeem"):
    kid = f"al-{uuid.uuid4().hex[:8]}"
    with get_conn() as c:
        c.execute(
            "INSERT INTO activity_log (id, project, action, detail, next_step, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, 'error', datetime('now'))",
            (kid, project, action, detail, next_step))
    return kid


def _bevinding(kaart_id, invariant, subject):
    with get_conn() as c:
        c.execute(
            "INSERT INTO integrity_findings (id, invariant, subject, project, detail, "
            "severity, first_seen, last_seen, escalated_id) "
            "VALUES (?, ?, ?, 'Systeem', 'x', 'blokkerend', datetime('now'), "
            "datetime('now'), ?)",
            (str(uuid.uuid4()), invariant, subject, kaart_id))


# ── 1. De kaart wordt aan zijn invariant gekoppeld, niet geraden ───────────

def test_invariant_via_escalated_id():
    kid = _kaart(detail="Iets heel anders dan de titel")
    _bevinding(kid, "cluster_kannibalisatie", f"c-{uuid.uuid4().hex[:6]}")
    inv = ig.invariant_voor_kaart(kid, "Iets heel anders dan de titel")
    assert inv is not None and inv.key == "cluster_kannibalisatie"


def test_invariant_terugval_op_titel():
    """Zonder bevinding (opgeruimd, oude kaart) mag de titel het nog redden."""
    inv_bron = next(i for i in ig.INVARIANTEN if i.key == "cluster_kannibalisatie")
    kid = _kaart(detail=f"{inv_bron.titel} — 9 geval(len): ...")
    inv = ig.invariant_voor_kaart(kid, f"{inv_bron.titel} — 9 geval(len): ...")
    assert inv is not None and inv.key == "cluster_kannibalisatie"


def test_onbekende_kaart_geeft_none():
    assert ig.invariant_voor_kaart("bestaat-niet", "geen enkele invarianttitel") is None


# ── 2. Een audit-kaart gaat nooit langs de LLM-whitelist ──────────────────

@pytest.mark.asyncio
async def test_audit_kaart_geeft_de_stap_uit_het_register(monkeypatch):
    inv = next(i for i in ig.INVARIANTEN if i.key == "cluster_kannibalisatie")
    kid = _kaart(detail=f"{inv.titel} — 9 geval(len)", next_step=inv.stap)
    _bevinding(kid, "cluster_kannibalisatie", f"c-{uuid.uuid4().hex[:6]}")

    async def _nooit(*a, **kw):  # pragma: no cover — moet niet aangeroepen worden
        raise AssertionError("de triage-LLM mag hier niet aan te pas komen")
    monkeypatch.setattr(triage, "_diagnose", _nooit)

    uit = await triage.analyze_and_fix(kid)
    assert uit["ok"] is True
    assert uit["remedy_type"] == "human_step"
    # Precies de stap van de invariant: "schrijf er géén nieuw artikel bij".
    assert uit["human_step"] == inv.stap
    assert "content_run" not in str(uit)


@pytest.mark.asyncio
async def test_audit_kaart_draait_wel_een_echte_remedie(monkeypatch):
    """Bestaat er wél een remedie, dan ís de klik de goedkeuring."""
    inv = next(i for i in ig.INVARIANTEN if i.key == "metatitel_afgekapt")
    kid = _kaart(detail=f"{inv.titel} — 2 geval(len)", next_step=inv.stap,
                 project="AuditProject")
    _bevinding(kid, "metatitel_afgekapt", f"job:{uuid.uuid4().hex[:6]}")

    gedraaid = {}

    async def _nep(project=None, maximum=25):
        gedraaid["project"] = project
        return {"gerepareerd": 2, "mislukt": []}

    from backend.domains.publish import repair
    monkeypatch.setitem(repair.REMEDIES, "metatitel_afgekapt", _nep)

    uit = await triage.analyze_and_fix(kid)
    assert uit["ok"] is True
    assert gedraaid["project"] == "AuditProject"
    assert "2 geval(len) gerepareerd" in uit["result"]


@pytest.mark.asyncio
async def test_menselijk_besluit_gebruikt_de_stap_van_de_kaart(monkeypatch):
    """Een gemiste run los je niet op met een contentronde."""
    kid = _kaart(action="gemiste_runs", detail="outreach_batch sloeg 4× over",
                 next_step="Draai de gemiste run alsnog met de knop op de kaart.")

    async def _nooit(*a, **kw):  # pragma: no cover
        raise AssertionError("geen LLM op een besluit dat alleen Vincent kan nemen")
    monkeypatch.setattr(triage, "_diagnose", _nooit)

    uit = await triage.analyze_and_fix(kid)
    assert uit["remedy_type"] == "human_step"
    assert "alsnog" in uit["human_step"]


# ── 3. Een lege remedie liegt niet, en slijt niet in ───────────────────────

@pytest.mark.asyncio
async def test_lege_remedie_verwijst_niet_naar_een_kaart_die_er_niet_is(monkeypatch):
    kid = _kaart(action="social_fetch", detail="ophalen mislukt", project="Proj")

    async def _diag(project, action, detail):
        return {"diagnose": "d", "remedy_type": "content_run", "target": "Proj", "aantal": 1}
    monkeypatch.setattr(triage, "_diagnose", _diag)

    from backend.domains.iris import actions

    async def _leeg(*a, **kw):
        return None
    monkeypatch.setattr(actions, "content_run", _leeg)

    uit = await triage.analyze_and_fix(kid)
    assert uit["ok"] is False
    assert "foutkaart hierboven" not in uit["result"]
    assert "leverde niets op" in uit["result"]


def test_remedie_zonder_succes_wordt_verleerd():
    sig = f"test::{uuid.uuid4().hex[:10]}"
    with get_conn() as c:
        c.execute(
            "INSERT INTO iris_error_fixes (id, signature, project, sample_action, "
            "sample_detail, diagnosis, remedy_type, remedy_payload, human_step, "
            "occurrences, attempts, successes, failures, created_at, updated_at) "
            "VALUES (?,?,'P','social_fetch','x','d','content_run','{}','',1,3,0,3,"
            "datetime('now'), datetime('now'))",
            (f"fix-{uuid.uuid4().hex[:8]}", sig))
    triage._verleer_bij_aanhoudend_falen(sig)
    with get_conn() as c:
        actief = c.execute("SELECT active FROM iris_error_fixes WHERE signature = ?",
                           (sig,)).fetchone()["active"]
    assert actief == 0


def test_invariant_ziet_een_remedie_die_nooit_iets_oploste():
    sig = f"test::{uuid.uuid4().hex[:10]}"
    with get_conn() as c:
        c.execute(
            "INSERT INTO iris_error_fixes (id, signature, project, sample_action, "
            "sample_detail, diagnosis, remedy_type, remedy_payload, human_step, "
            "occurrences, attempts, successes, failures, active, created_at, updated_at) "
            "VALUES (?,?,'P','social_fetch','x','d','content_run','{}','',1,4,0,4,1,"
            "datetime('now'), datetime('now'))",
            (f"fix-{uuid.uuid4().hex[:8]}", sig))
    try:
        gevonden = [b for b in ig._check_triage_remedie_zonder_effect()
                    if sig[:20] in b.subject]
        assert len(gevonden) == 1
        assert "zonder één succes" in gevonden[0].detail
    finally:
        with get_conn() as c:
            c.execute("DELETE FROM iris_error_fixes WHERE signature = ?", (sig,))


def test_zelfherstel_probes_tellen_hier_niet_mee():
    """`selfheal` deelt de tabel maar stopt zelf al na drie pogingen."""
    sig = f"test::{uuid.uuid4().hex[:10]}"
    with get_conn() as c:
        c.execute(
            "INSERT INTO iris_error_fixes (id, signature, project, sample_action, "
            "sample_detail, diagnosis, remedy_type, remedy_payload, human_step, "
            "occurrences, attempts, successes, failures, active, created_at, updated_at) "
            "VALUES (?,?,'P','publicatie_mislukt','x','d','probe','{}','',1,4,0,4,1,"
            "datetime('now'), datetime('now'))",
            (f"fix-{uuid.uuid4().hex[:8]}", sig))
    try:
        assert not [b for b in ig._check_triage_remedie_zonder_effect()
                    if sig[:20] in b.subject]
    finally:
        with get_conn() as c:
            c.execute("DELETE FROM iris_error_fixes WHERE signature = ?", (sig,))


def test_een_werkende_remedie_blijft_met_rust():
    sig = f"test::{uuid.uuid4().hex[:10]}"
    with get_conn() as c:
        c.execute(
            "INSERT INTO iris_error_fixes (id, signature, project, sample_action, "
            "sample_detail, diagnosis, remedy_type, remedy_payload, human_step, "
            "occurrences, attempts, successes, failures, active, created_at, updated_at) "
            "VALUES (?,?,'P','social_fetch','x','d','content_run','{}','',1,4,1,3,1,"
            "datetime('now'), datetime('now'))",
            (f"fix-{uuid.uuid4().hex[:8]}", sig))
    try:
        triage._verleer_bij_aanhoudend_falen(sig)
        with get_conn() as c:
            actief = c.execute("SELECT active FROM iris_error_fixes WHERE signature = ?",
                               (sig,)).fetchone()["active"]
        assert actief == 1
        assert not [b for b in ig._check_triage_remedie_zonder_effect()
                    if sig[:20] in b.subject]
    finally:
        with get_conn() as c:
            c.execute("DELETE FROM iris_error_fixes WHERE signature = ?", (sig,))
