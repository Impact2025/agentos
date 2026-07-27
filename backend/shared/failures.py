"""Faal-classificatie — één waarheid over "is dit een blip of moet er iemand ingrijpen?".

Zonder deze scheiding gebeurt precies wat op 25 jul 2026 gebeurde: een
nachtelijke TLS-blip (laptop sliep, netwerk weg) leverde een rode
"WACHT OP JOU"-kaart op met de tekst *"Ophalen van instagram mislukt:"* — leeg,
want `str(httpx.ConnectError())` is een lege string — én de raad "controleer de
kanaal-tokens", terwijl de tokens niets mankeerden. Tegelijk was het écht
kapotte ding (een IG-token dat op 13 juli verliep, HTTP 400/OAuthException 190)
al twaalf dagen onzichtbaar, omdat de fetch bij een non-200 stil `[]` teruggaf.
Precies verkeerd om: ruis waar niets aan te doen valt, stilte waar een mens
nodig is.

Twee functies dekken dat af:

- `describe_exception()` geeft NOOIT een lege tekst terug. Een exception zonder
  boodschap (de hele httpx/anyio-familie) wordt vertaald naar wat er feitelijk
  misging, inclusief de onderliggende oorzaak (`__cause__`).
- `classify()` deelt een fout in bij wie hem kan oplossen: `transient` (wacht en
  probeer opnieuw — een agent lost dit zelf op), `auth`/`config` (alleen een
  mens), `quota`/`ratelimit` (tijd), of `unknown` (dan mag Iris' LLM-triage
  ernaar kijken).

De faal-reeks (`note_failure`/`note_success`) is het geheugen dat "probeer het
zelf" van "meld het" scheidt: één mislukte poging is geen storing, drie op rij
wél. Reeksen leven in SQLite en niet in het procesgeheugen, want anders is na
een herstart "nooit gefaald" niet te onderscheiden van "faalt al uren".
"""
from __future__ import annotations

import logging
from typing import Optional

from .database import get_conn

logger = logging.getLogger(__name__)

CLASS_TRANSIENT = "transient"
CLASS_AUTH = "auth"
CLASS_QUOTA = "quota"
CLASS_RATELIMIT = "ratelimit"
CLASS_CONFIG = "config"
CLASS_UNKNOWN = "unknown"

# Klassen die geen enkele agent kan oplossen — meteen melden heeft zin, blijven
# proberen niet.
HUMAN_ONLY = (CLASS_AUTH, CLASS_CONFIG)

# Hoeveel mislukkingen op rij vóórdat een transiente fout tóch een mens vraagt.
# Drie polls van 30 minuten = anderhalf uur echt-weg; dat is geen blip meer.
DEFAULT_ESCALATE_AFTER = 3

# Exception-types die zichzelf niet uitleggen. `str()` is bij deze klassen vaak
# leeg (httpx wikkelt een httpcore-fout die zelf al leeg was), dus de vertaling
# moet uit de klassenaam komen.
_TYPE_TEXT = {
    "ConnectError": "geen verbinding met de server (netwerk weg of DNS onbereikbaar)",
    "ConnectTimeout": "verbinding maken duurde te lang (netwerk traag of weg)",
    "ReadTimeout": "de server antwoordde niet binnen de tijdslimiet",
    "WriteTimeout": "versturen duurde te lang",
    "PoolTimeout": "geen vrije verbinding beschikbaar binnen de tijdslimiet",
    "ReadError": "de verbinding brak af tijdens het lezen",
    "WriteError": "de verbinding brak af tijdens het versturen",
    "RemoteProtocolError": "de server verbrak de verbinding zonder antwoord",
    "RemoteDisconnected": "de server verbrak de verbinding zonder antwoord",
    "ConnectionResetError": "de verbinding werd door de andere kant gereset",
    "BrokenResourceError": "de verbinding brak tijdens de TLS-handshake",
    "EndOfStream": "de verbinding brak tijdens de TLS-handshake",
    "SSLError": "de beveiligde verbinding (TLS) kwam niet tot stand",
    "ProtocolError": "de server sprak een onverwacht protocol",
    "CancelledError": "de bewerking werd afgebroken",
    "TimeoutError": "de bewerking liep in een tijdslimiet",
}

# Tekstsignalen per klasse. Op volgorde getoetst: de eerste die raakt wint, dus
# specifiek vóór algemeen (een 401 met het woord "quota" is een quota-probleem).
_SIGNALS = (
    (CLASS_QUOTA, (
        "quota exceeded", "quota op", "insufficient_quota", "out of credits",
        "billing", "dagbudget", "credit balance",
    )),
    (CLASS_RATELIMIT, (
        "rate limit", "ratelimit", "too many requests", "429",
    )),
    (CLASS_AUTH, (
        "oauthexception", "access token", "session has expired", "token expired",
        "token verlopen", "invalid_grant", "invalid credentials", "unauthorized",
        "401", "403", "permission denied", "geen rechten", "authentication",
        "verlopen op", "re-authenticate", "invalid api key", "api key",
    )),
    (CLASS_CONFIG, (
        "not configured", "niet geconfigureerd", "ontbreekt", "missing",
        "no such file", "bestaat niet", "not found in .env", "geen sitedata",
    )),
    (CLASS_TRANSIENT, (
        "connection aborted", "connection reset", "remotedisconnected",
        "connectionreseterror", "temporarily unavailable", "timed out",
        "timeout", "getaddrinfo", "[errno 11001]", "[errno 11002]",
        "name or service not known", "dns", "handshake", "ssl", "eof occurred",
        "connection refused", "network is unreachable", "bad gateway", "502",
        "503", "504", "service unavailable", "server verbrak",
        "geen verbinding", "netwerk weg", "brak af", "tls",
    )),
)

# Exception-types die per definitie transient zijn, wat de tekst ook zegt.
_TRANSIENT_TYPES = (
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ReadError", "WriteError", "RemoteProtocolError", "RemoteDisconnected",
    "ConnectionResetError", "ConnectionAbortedError", "BrokenResourceError",
    "EndOfStream", "TimeoutError", "TransportError",
)


def _type_names(exc: BaseException) -> list[str]:
    """Klassenamen van de exception én de hele __cause__/__context__-ketting.

    httpx wikkelt httpcore wikkelt anyio wikkelt ssl: de bruikbare naam zit
    zelden bovenaan.
    """
    names: list[str] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen and len(names) < 8:
        seen.add(id(cur))
        names.append(type(cur).__name__)
        for base in type(cur).__mro__[1:]:
            if base.__name__ in ("Exception", "BaseException", "object"):
                break
            names.append(base.__name__)
        cur = cur.__cause__ or cur.__context__
    return names


def describe_exception(exc: BaseException) -> str:
    """Een altijd-gevulde, leesbare omschrijving van wat er misging.

    Een lege `str(exc)` mag nooit als lege foutmelding in de UI belanden: dan
    staat er "mislukt:" en weet niemand — mens noch Iris — waar te beginnen.
    """
    if exc is None:  # defensief: aanroepers geven soms None door
        return "onbekende fout"
    text = str(exc).strip()
    if text and text not in ("()", "''", '""'):
        return text
    for name in _type_names(exc):
        if name in _TYPE_TEXT:
            return _TYPE_TEXT[name]
    # Laatste redmiddel: de klassenaam is lelijk maar nooit nietszeggend.
    return f"{type(exc).__name__} (zonder foutmelding)"


def classify(err: object) -> str:
    """Deel een fout in bij wie hem kan oplossen.

    Accepteert een exception of een kale tekst (zoals opgeslagen in
    `activity_log.detail` of `scheduler_runs.error`), zodat dezelfde regels
    gelden op het moment van falen én later bij Iris' zelfherstel-ronde.
    """
    if isinstance(err, BaseException):
        names = _type_names(err)
        text = f"{describe_exception(err)} {' '.join(names)}".lower()
        # Een HTTP-statusfout weet zijn eigen code beter dan de tekst.
        resp = getattr(err, "response", None)
        code = getattr(resp, "status_code", None)
        if code in (401, 403):
            return CLASS_AUTH
        if code == 429:
            return CLASS_RATELIMIT
        if code in (500, 502, 503, 504):
            return CLASS_TRANSIENT
        if any(n in _TRANSIENT_TYPES for n in names) and not any(
            s in text for s in ("quota", "token", "oauth")
        ):
            return CLASS_TRANSIENT
    else:
        text = str(err or "").lower()
    if not text.strip():
        return CLASS_UNKNOWN
    for klass, signals in _SIGNALS:
        if any(s in text for s in signals):
            return klass
    return CLASS_UNKNOWN


def is_transient(err: object) -> bool:
    return classify(err) == CLASS_TRANSIENT


def is_human_only(err: object) -> bool:
    """True als geen enkele agent dit kan oplossen (ontbrekende/verlopen
    credentials, kapotte configuratie). Dan is meteen melden juist — blijven
    proberen kost alleen tijd."""
    return classify(err) in HUMAN_ONLY


# ── Faal-reeksen: het geheugen achter "probeer zelf, meld pas als het echt
#    niet lukt" ────────────────────────────────────────────────────────────

def note_failure(key: str, detail: str, failure_class: str = "") -> int:
    """Registreer één mislukking en geef terug hoe vaak het nu op rij misging."""
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_failure_streaks
                    (key, fail_count, first_failed_at, last_failed_at, last_detail, failure_class)
                VALUES (?, 1, datetime('now'), datetime('now'), ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    fail_count     = agent_failure_streaks.fail_count + 1,
                    last_failed_at = datetime('now'),
                    last_detail    = excluded.last_detail,
                    failure_class  = excluded.failure_class
                """,
                (key, (detail or "")[:400], failure_class or ""),
            )
            row = conn.execute(
                "SELECT fail_count FROM agent_failure_streaks WHERE key = ?", (key,)
            ).fetchone()
        return int(row["fail_count"]) if row else 1
    except Exception:
        # Het bijhouden van de reeks mag de aanroeper nooit laten omvallen; bij
        # twijfel doen we alsof dit de eerste keer is (dus: zelf proberen).
        logger.exception("Kon faal-reeks '%s' niet bijwerken", key)
        return 1


def note_success(key: str) -> int:
    """Wis de reeks na een geslaagde poging. Retourneert de reeks die eindigde
    (0 = er liep geen storing), zodat de aanroeper "hersteld"-nieuws kan melden."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT fail_count FROM agent_failure_streaks WHERE key = ?", (key,)
            ).fetchone()
            had = int(row["fail_count"]) if row else 0
            if had:
                conn.execute("DELETE FROM agent_failure_streaks WHERE key = ?", (key,))
        return had
    except Exception:
        logger.exception("Kon faal-reeks '%s' niet wissen", key)
        return 0


def streak(key: str) -> dict:
    """De huidige reeks (leeg dict als er niets loopt)."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_failure_streaks WHERE key = ?", (key,)
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def mark_escalated(key: str) -> None:
    """Onthoud dat dit al gemeld is — anders krijgt Vincent elke poll dezelfde
    rode kaart erbij, en dat is precies hoe je leert de kaarten te negeren."""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE agent_failure_streaks SET escalated = 1 WHERE key = ?", (key,)
            )
    except Exception:
        logger.exception("Kon escalatie-vlag '%s' niet zetten", key)


def should_escalate(key: str, err: object, *, after: int = DEFAULT_ESCALATE_AFTER) -> bool:
    """Moet deze fout een mens wakker maken?

    Ja bij een mens-alleen klasse (wachten helpt daar niet), of zodra de reeks
    lang genoeg is. Nee als er al voor geëscaleerd is.
    """
    st = streak(key)
    if st.get("escalated"):
        return False
    if is_human_only(err):
        return True
    return int(st.get("fail_count") or 0) >= after
