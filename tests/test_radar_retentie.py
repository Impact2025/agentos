"""Radar-signalen zijn een momentopname en horen te verlopen.

Achtergrond (27 juli 2026): in drie weken stonden er 2656 signalen, waarvan 2411
nog op 'new'. Een trend van vijf weken geleden die toen al onder de vault-drempel
bleef gaat vandaag niemand meer aanvallen, dus die rijen groeien monotoon zonder
ooit iets op te leveren.

Bewust níét de drempels verhoogd: die zijn op 17 juli gekalibreerd (68→40
auto-attack, 41% minder vals-positief) en het dure deel — de auto-attack — is al
gecapt op 3 per scan en gegate op score 75 + match 60. Er valt daar niets te
winnen; het gaat puur om tabelgroei.
"""
import uuid

import pytest

from backend.domains.radar import models as radar_models
from backend.domains.radar import service as radar
from backend.shared.database import get_conn

# De radar-tabellen staan niet in de gedeelde migratie maar in het domein zelf;
# in productie maakt de router ze aan bij het opstarten.
radar_models.ensure_schema()


def _signaal(status: str = "new", score: float = 40.0, dagen_oud: int = 60) -> str:
    sid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO radar_signals (id, project, keyword, title, url, source, "
            "snippet, signal_score, status, scanned_at, created_at, updated_at) "
            "VALUES (?, 'Test', 'kw', 'Titel', ?, 'test', '', ?, ?, "
            "datetime('now'), datetime('now', ?), datetime('now'))",
            (sid, f"https://voorbeeld.nl/{sid}", score, status, f"-{dagen_oud} days"),
        )
    return sid


def _bestaat(sid: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM radar_signals WHERE id = ?", (sid,)
        ).fetchone() is not None


@pytest.fixture(autouse=True)
def _schoon():
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM radar_signals WHERE project = 'Test'")


class TestOpruimen:
    def test_oud_laagscorend_en_ongetriageerd_gaat_weg(self):
        sid = _signaal(status="new", score=40.0, dagen_oud=60)
        radar.prune_stale_signals()
        assert not _bestaat(sid)

    def test_recent_signaal_blijft(self):
        sid = _signaal(status="new", score=40.0, dagen_oud=3)
        radar.prune_stale_signals()
        assert _bestaat(sid)

    def test_topsignaal_blijft_ook_als_het_oud_is(self):
        """Een hoog signaal dat niemand oppakte is een gemiste kans, geen ruis."""
        sid = _signaal(status="new", score=85.0, dagen_oud=90)
        radar.prune_stale_signals()
        assert _bestaat(sid)

    @pytest.mark.parametrize("status", ["targeted", "converted", "dismissed"])
    def test_besluiten_blijven_staan(self, status):
        """'dismissed' moet blijven, anders biedt de volgende scan hetzelfde
        signaal gewoon opnieuw aan."""
        sid = _signaal(status=status, score=40.0, dagen_oud=90)
        radar.prune_stale_signals()
        assert _bestaat(sid)

    def test_alleen_de_juiste_rijen_verdwijnen(self):
        weg_a = _signaal(status="new", score=10.0, dagen_oud=60)
        weg_b = _signaal(status="new", score=20.0, dagen_oud=60)
        blijft_vers = _signaal(status="new", score=10.0, dagen_oud=1)
        blijft_besluit = _signaal(status="targeted", score=10.0, dagen_oud=60)

        radar.prune_stale_signals()

        # Op identiteit toetsen en niet op het aantal: prune_stale_signals werkt
        # over álle projecten, dus een telling zou meeliften op signalen die
        # andere tests hebben achtergelaten.
        assert not _bestaat(weg_a) and not _bestaat(weg_b)
        assert _bestaat(blijft_vers) and _bestaat(blijft_besluit)

    def test_bewaartermijn_is_instelbaar(self):
        sid = _signaal(status="new", score=10.0, dagen_oud=10)
        radar.prune_stale_signals(days=90)
        assert _bestaat(sid), "10 dagen oud valt binnen een termijn van 90 dagen"
        radar.prune_stale_signals(days=5)
        assert not _bestaat(sid)
