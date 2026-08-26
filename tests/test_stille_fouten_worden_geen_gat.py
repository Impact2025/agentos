"""Tests voor twee `except Exception: pass`-blokken die volgens CLAUDE.md
verboden zijn ("falen álle providers, dan luid — nooit stil") en die
precies in domeinen zaten met een geschiedenis van stille storingen
(Agent Control: de teruggezette-teller-incident van 15 aug; radar: de
signaalpoort/trendbrug-stiltes van 7d).

Beide fixes onderscheiden "verwacht, stil overslaan" (tabel bestaat nog
niet op een verse installatie / pakket niet geïnstalleerd) van "een echt
probleem dat gelogd moet worden" — dit bestand toetst dat onderscheid.
"""
import logging
import sqlite3

import pytest

from backend.domains.agentctl import service as agentctl_service
from backend.shared.database import get_conn


# ── agentctl/service.py: _active_workloads over delegate_workers ───────────

def test_active_workloads_negeert_ontbrekende_delegate_workers_tabel(monkeypatch, caplog):
    """delegate_workers wordt lazy aangemaakt door het delegate-domein — een
    verse installatie die dat domein nog nooit heeft aangeraakt mag geen
    foutmelding geven."""
    import backend.shared.database as dbmod

    real_get_conn = dbmod.get_conn

    class _BrokenConn:
        def __enter__(self):
            self._cm = real_get_conn()
            self._conn = self._cm.__enter__()
            return self

        def __exit__(self, *a):
            return self._cm.__exit__(*a)

        def execute(self, sql, *a, **kw):
            if "delegate_workers" in sql:
                raise sqlite3.OperationalError("no such table: delegate_workers")
            return self._conn.execute(sql, *a, **kw)

    monkeypatch.setattr(agentctl_service, "get_conn", lambda: _BrokenConn())
    with caplog.at_level(logging.WARNING):
        result = agentctl_service._active_workloads()
    assert isinstance(result, dict)
    assert "delegate_workers" not in caplog.text


def test_active_workloads_logt_echte_sql_fout_op_delegate_workers(monkeypatch, caplog):
    """Een échte SQL-fout (niet 'tabel bestaat niet') mag niet verdwijnen —
    precies het soort stille storing waar Agent Control voor bewaakt."""
    import backend.shared.database as dbmod

    real_get_conn = dbmod.get_conn

    class _BrokenConn:
        def __enter__(self):
            self._cm = real_get_conn()
            self._conn = self._cm.__enter__()
            return self

        def __exit__(self, *a):
            return self._cm.__exit__(*a)

        def execute(self, sql, *a, **kw):
            if "delegate_workers" in sql:
                raise sqlite3.OperationalError("database disk image is malformed")
            return self._conn.execute(sql, *a, **kw)

    monkeypatch.setattr(agentctl_service, "get_conn", lambda: _BrokenConn())
    with caplog.at_level(logging.ERROR):
        agentctl_service._active_workloads()
    assert "delegate_workers" in caplog.text


# ── radar/service.py: Tavily-client-initialisatie ───────────────────────────

def test_tavily_init_faalt_stil_zonder_package(monkeypatch, caplog):
    from backend.domains import radar as radar_pkg  # noqa: F401
    from backend.domains.radar import service as radar_service_mod

    monkeypatch.setattr(radar_service_mod, "TAVILY_API_KEY", "een-key")

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "tavily":
            raise ImportError("No module named 'tavily'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level(logging.WARNING):
        svc = radar_service_mod.RadarService()
    assert svc._tavily is None
    assert "Tavily" not in caplog.text


def test_tavily_init_logt_echte_configuratiefout(monkeypatch, caplog):
    from backend.domains.radar import service as radar_service_mod

    monkeypatch.setattr(radar_service_mod, "TAVILY_API_KEY", "een-key")

    class _BoomClient:
        def __init__(self, api_key):
            raise ValueError("ongeldige API-key structuur")

    import tavily
    monkeypatch.setattr(tavily, "TavilyClient", _BoomClient)
    with caplog.at_level(logging.WARNING):
        svc = radar_service_mod.RadarService()
    assert svc._tavily is None
    assert "Tavily" in caplog.text
