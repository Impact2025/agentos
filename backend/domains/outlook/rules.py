"""
Afzenderregels — welke post hoort niet in het postvak, en wie bepaalt dat.

Tot 11 aug 2026 stond alle filtering als Python-lijst in `mail/classify.py`
(`VENDOR_NOISE_DOMAINS`). Drie dingen waren daar mis mee, en alle drie zijn ze
de reden dat dit bestand bestaat:

1. **De regels waren niet van Vincent.** Er was geen knop, geen tabel, geen
   endpoint: "deze afzender nooit meer" kon alleen door de code te wijzigen.
2. **Ze werkten niet met terugwerkende kracht.** `is_inbox_noise` draait in
   `triage_single`, dus een regel die vandaag wordt toegevoegd raakt alleen mail
   die daarna binnenkomt. De veertien mails die er al staan blijven staan — en
   dat is precies het moment waarop een filter zijn belofte breekt.
3. **Eén regel was gevaarlijk breed.** `weareimpact.nl` stond in de lijst met de
   motivering "eigen geautomatiseerde mailingen", maar dat is Vincents eigen
   bedrijfsdomein: mail van een collega verdween stil naar archief. De echte
   oorzaak van die mails was iets anders (zie service.sync_inbox: we haalden de
   hele mailbox op in plaats van het postvak IN), en een te breed filter was
   daar het verkeerde antwoord op.

Ontwerp, in dezelfde geest als `seo/opportunity_quality.py`:

* **Deterministisch, geen LLM.** Een filter dat een gateway nodig heeft valt
  stil precies wanneer de gateway plat ligt.
* **Eén mechanisme.** De oude code-lijsten zijn hier de *seed* van; ze zijn
  daarmee zichtbaar, telbaar en uit te zetten in plaats van onzichtbaar waar.
* **Niets verdwijnt stil.** Een geweerde mail krijgt `triage_label='spam'` of
  `'archief'` mét `filter_reason` en `filter_rule_id`, blijft opvraagbaar, en
  het intrekken van een regel geeft élke mail die eraan sneuvelde terug.
* **De whitelist wint.** `altijd-tonen` staat boven elke andere regel, anders
  is het filter alleen strenger te maken en nooit milder.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

# ── Vocabulaire ────────────────────────────────────────────────────────────
# Scope zegt wáár het patroon op slaat. Een mens die op "nooit meer" tikt
# krijgt altijd `adres` of `domein`; `deel` bestaat alleen voor de geërfde
# systeemregels (substring op het volledige adres), want die waren zo geschreven.
SCOPE_ADRES = "adres"
SCOPE_DOMEIN = "domein"
SCOPE_DEEL = "deel"
_SCOPES = (SCOPE_ADRES, SCOPE_DOMEIN, SCOPE_DEEL)

# Actie zegt wát ermee moet. Twee soorten "weg" die bewust gescheiden blijven:
# spam is ongewenst (die afzender had nooit mogen mailen), geen-klant is
# legitieme post die alleen niet op een besluit van Vincent wacht. Ze landen in
# een ander label, en dat verschil is precies wat de bak "Uitgefilterd" leesbaar
# houdt: 21 nieuwsbrieven en 2 phishingmails horen niet op één hoop.
ACTIE_SPAM = "spam"
ACTIE_GEEN_KLANT = "geen-klant"
ACTIE_ALTIJD_TONEN = "altijd-tonen"
_ACTIES = (ACTIE_SPAM, ACTIE_GEEN_KLANT, ACTIE_ALTIJD_TONEN)

# Naar welk triage-label een actie de mail zet. 'archief' bestond al als
# triage-label (zie _TRIAGE_SYSTEM); 'spam' is nieuw en bewust apart — een mail
# die je nooit meer wilt zien is iets anders dan een mail die je wel had willen
# krijgen maar niet hoeft te beantwoorden.
_LABEL_VOOR_ACTIE = {ACTIE_SPAM: "spam", ACTIE_GEEN_KLANT: "archief"}

# Labels die betekenen "door een regel weggehouden" — gebruikt door elke lezer
# die de échte inbox wil tellen.
GEFILTERDE_LABELS = ("spam", "archief")

# Een mail die een mens expliciet heeft teruggezet. Zonder deze markering haalt
# de eerstvolgende ronde van `apply_all` hem meteen weer weg — de regel matcht
# immers nog steeds — en dan doet "toch tonen" niets, één keer per twintig
# minuten. Terugzetten is een besluit over déze mail; de regel intrekken is een
# besluit over de afzender. Beide bestaan, en ze horen elkaar niet te overrulen.
HANDMATIG_TERUG = "handmatig teruggezet"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _domein(adres: str) -> str:
    adres = (adres or "").lower().strip()
    m = re.search(r"@([^>\s]+)$", adres)
    return m.group(1) if m else ""


# ── Regels lezen/schrijven ─────────────────────────────────────────────────

def list_rules(include_inactive: bool = False) -> List[dict]:
    """Alle regels, meest recent eerst; systeemregels achteraan.

    Twee sorteersleutels omdat het twee verschillende lijsten zijn voor een
    mens: wat híj heeft ingesteld staat bovenaan, de geërfde standaardregels
    eronder (die zijn er tientallen en je scrolt er niet doorheen).
    """
    where = "" if include_inactive else "WHERE active = 1"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM mail_sender_rules {where} "
            "ORDER BY (source='systeem') ASC, created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_rule(rule_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mail_sender_rules WHERE id = ?", (int(rule_id),)
        ).fetchone()
    return dict(row) if row else None


def _normaliseer(pattern: str, scope: str) -> str:
    """Een patroon dat de gebruiker intikt of dat uit een mailrij komt.

    Bij scope 'domein' accepteren we bewust ook een volledig adres — dat is wat
    je in de hand hebt als je op een mailregel tikt, en de bedoeling ("alles van
    dit domein") is dan ondubbelzinnig.
    """
    p = (pattern or "").lower().strip().lstrip("@")
    if scope == SCOPE_DOMEIN and "@" in p:
        p = _domein(p) or p
    return p


def add_rule(pattern: str, *, scope: str = SCOPE_ADRES, action: str = ACTIE_SPAM,
             reason: str = "", source: str = "mens") -> dict:
    """Regel toevoegen en meteen toepassen op wat er al ligt.

    Het toepassen zit bewust in dezelfde functie: een regel die pas werkt bij de
    volgende binnenkomende mail is geen filter maar een belofte, en juist het
    opruimen van de stapel die er nú staat is waarom iemand op de knop drukt.
    """
    scope = scope if scope in _SCOPES else SCOPE_ADRES
    action = action if action in _ACTIES else ACTIE_SPAM
    p = _normaliseer(pattern, scope)
    if not p:
        raise ValueError("Leeg patroon — regel niet aangemaakt")

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO mail_sender_rules (pattern, scope, action, reason, source, "
            "                               active, created_at) "
            "VALUES (?,?,?,?,?,1,?) "
            "ON CONFLICT(pattern, scope) DO UPDATE SET "
            "    action = excluded.action, reason = excluded.reason, "
            "    source = excluded.source, active = 1",
            (p, scope, action, reason, source, _now()),
        )
        row = conn.execute(
            "SELECT * FROM mail_sender_rules WHERE pattern = ? AND scope = ?", (p, scope)
        ).fetchone()

    rule = dict(row)
    rule["applied"] = apply_rule(rule)
    return rule


def deactivate_rule(rule_id: int) -> dict:
    """Regel intrekken en élke mail teruggeven die eraan is gesneuveld.

    Teruggeven = het triage-label leegmaken, niet raden wat het geweest zou
    zijn: de mail gaat gewoon opnieuw door de triage. Een gegokt label zou een
    mail die vier weken geleden urgent was vandaag als urgent terugzetten.
    """
    rule = get_rule(rule_id)
    if not rule:
        raise ValueError(f"Regel {rule_id} bestaat niet")
    with get_conn() as conn:
        conn.execute("UPDATE mail_sender_rules SET active = 0 WHERE id = ?", (int(rule_id),))
        cur = conn.execute(
            "UPDATE outlook_emails SET triage_label = '', priority = 50, "
            "       filter_reason = '', filter_rule_id = NULL, triaged_at = '' "
            "WHERE filter_rule_id = ?",
            (int(rule_id),),
        )
        vrijgegeven = cur.rowcount or 0
    return {**rule, "active": 0, "released": vrijgegeven}


# ── Matchen ────────────────────────────────────────────────────────────────

def _matcht(rule: dict, adres: str, domein: str) -> bool:
    p = rule["pattern"]
    scope = rule["scope"]
    if scope == SCOPE_ADRES:
        return adres == p
    if scope == SCOPE_DOMEIN:
        return domein == p or domein.endswith("." + p)
    return bool(p) and p in adres


def match(from_email: str, rules: Optional[List[dict]] = None) -> Optional[dict]:
    """De regel die op dit adres van toepassing is, of None.

    Volgorde is het hele ontwerp: eerst de whitelist (`altijd-tonen`), dan het
    meest specifieke patroon. Zonder die volgorde kan een mens een systeemregel
    die te breed is niet overrulen zonder hem te verwijderen, en dan is het
    filter alleen strenger te maken.
    """
    adres = (from_email or "").lower().strip()
    if not adres:
        return None
    domein = _domein(adres)
    kandidaten = list_rules() if rules is None else rules

    volgorde = {SCOPE_ADRES: 0, SCOPE_DOMEIN: 1, SCOPE_DEEL: 2}
    for whitelist_ronde in (True, False):
        for rule in sorted(kandidaten, key=lambda r: volgorde.get(r["scope"], 9)):
            if (rule["action"] == ACTIE_ALTIJD_TONEN) != whitelist_ronde:
                continue
            if _matcht(rule, adres, domein):
                return None if whitelist_ronde else rule
    return None


def verdict(from_email: str, rules: Optional[List[dict]] = None) -> Optional[dict]:
    """Wat er met een binnenkomende mail moet gebeuren: {label, reason, rule_id}
    of None als hij gewoon door de triage mag."""
    rule = match(from_email, rules)
    if not rule:
        return None
    label = _LABEL_VOOR_ACTIE.get(rule["action"])
    if not label:
        return None
    return {
        "label": label,
        "reason": rule["reason"] or _standaard_reden(rule),
        "rule_id": rule["id"],
        "action": rule["action"],
    }


def _standaard_reden(rule: dict) -> str:
    wat = "spam" if rule["action"] == ACTIE_SPAM else "geen potentiële klant"
    door = "regel van jou" if rule["source"] == "mens" else "standaardregel"
    return f"{wat} — {door}: {rule['pattern']}"


# ── Toepassen ──────────────────────────────────────────────────────────────

def _tel_hit(conn, rule_id: int, aantal: int) -> None:
    if aantal <= 0:
        return
    conn.execute(
        "UPDATE mail_sender_rules SET hits = hits + ?, last_hit_at = ? WHERE id = ?",
        (aantal, _now(), int(rule_id)),
    )


def apply_rule(rule: dict) -> int:
    """Pas één regel toe op de mail die er al ligt. Geeft het aantal geraakte
    mails terug — dát is wat de knop moet terugmelden ("14 mails opgeruimd"),
    want een regel die niets deed voelt als een knop die niet werkte."""
    if not rule.get("active", 1):
        return 0

    if rule["action"] == ACTIE_ALTIJD_TONEN:
        # Een whitelist-regel maakt eerder gefilterde mail van deze afzender weer
        # zichtbaar; hij mag alleen mail raken die dóór een regel is weggehouden,
        # nooit iets wat de triage zelf op 'archief' zette.
        doel_label, reason, rule_id = "", "", None
        alleen_gefilterd = True
    else:
        doel_label = _LABEL_VOOR_ACTIE[rule["action"]]
        reason = rule["reason"] or _standaard_reden(rule)
        rule_id = rule["id"]
        alleen_gefilterd = False

    geraakt = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, from_email, triage_label, filter_rule_id FROM outlook_emails "
            "WHERE folder = 'inbox' AND COALESCE(filter_reason,'') != ?",
            (HANDMATIG_TERUG,),
        ).fetchall()
        for row in rows:
            adres = (row["from_email"] or "").lower()
            if not _matcht(rule, adres, _domein(adres)):
                continue
            if alleen_gefilterd:
                if row["filter_rule_id"] is None:
                    continue
                conn.execute(
                    "UPDATE outlook_emails SET triage_label='', priority=50, "
                    "       filter_reason='', filter_rule_id=NULL, triaged_at='' WHERE id=?",
                    (row["id"],),
                )
            else:
                if row["triage_label"] == doel_label and row["filter_rule_id"] == rule_id:
                    continue
                conn.execute(
                    "UPDATE outlook_emails SET triage_label=?, priority=0, "
                    "       filter_reason=?, filter_rule_id=?, triaged_at=?, "
                    "       suggested_reply='', suggested_reply_at='' WHERE id=?",
                    (doel_label, reason, rule_id, _now(), row["id"]),
                )
            geraakt += 1
        if not alleen_gefilterd:
            _tel_hit(conn, rule["id"], geraakt)
    return geraakt


def apply_all(only_untriaged: bool = False) -> int:
    """Alle actieve regels over het postvak halen.

    Draait bij elke sync (nieuwe mail) en na een migratie. `only_untriaged`
    beperkt tot verse mail — dat is het goedkope pad voor de sync-lus; de volle
    variant is voor het moment dat de regels zélf veranderd zijn.
    """
    regels = [r for r in list_rules() if r["action"] != ACTIE_ALTIJD_TONEN]
    if not regels:
        return 0
    whitelist = [r for r in list_rules() if r["action"] == ACTIE_ALTIJD_TONEN]

    clause = "WHERE folder='inbox' AND COALESCE(filter_reason,'') != ?"
    params = [HANDMATIG_TERUG]
    if only_untriaged:
        clause += " AND triage_label = ''"

    geraakt = 0
    per_regel: Dict[int, int] = {}
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, from_email, triage_label, filter_rule_id FROM outlook_emails {clause}",
            params,
        ).fetchall()
        for row in rows:
            oordeel = verdict(row["from_email"], regels + whitelist)
            if not oordeel:
                continue
            if row["triage_label"] == oordeel["label"] and row["filter_rule_id"] == oordeel["rule_id"]:
                continue
            conn.execute(
                "UPDATE outlook_emails SET triage_label=?, priority=0, filter_reason=?, "
                "       filter_rule_id=?, triaged_at=?, suggested_reply='', "
                "       suggested_reply_at='' WHERE id=?",
                (oordeel["label"], oordeel["reason"], oordeel["rule_id"], _now(), row["id"]),
            )
            per_regel[oordeel["rule_id"]] = per_regel.get(oordeel["rule_id"], 0) + 1
            geraakt += 1
        for rid, aantal in per_regel.items():
            _tel_hit(conn, rid, aantal)
    return geraakt


# ── Wat de regels hebben weggehouden ───────────────────────────────────────

def filtered_stats(days: int = 7) -> dict:
    """Cijfers voor het scherm 'Geblokkeerde afzenders'.

    Zonder deze terugkoppeling is een filter niet te beoordelen: je ziet alleen
    wat er nog staat, nooit wat er weg is, en dan kun je niet weten of het te
    streng staat. Daarom óók een top van de regels die het meeste raakten — een
    regel die in zijn eentje honderd mails wegneemt verdient een blik.
    """
    sinds = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        week = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails "
            "WHERE filter_rule_id IS NOT NULL AND received_at >= ?", (sinds,)
        ).fetchone()["c"]
        totaal = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails WHERE filter_rule_id IS NOT NULL"
        ).fetchone()["c"]
        top = conn.execute(
            "SELECT r.id, r.pattern, r.scope, r.action, r.source, COUNT(e.id) c "
            "FROM mail_sender_rules r JOIN outlook_emails e ON e.filter_rule_id = r.id "
            "WHERE e.received_at >= ? GROUP BY r.id ORDER BY c DESC LIMIT 5", (sinds,)
        ).fetchall()
    return {"days": days, "blocked_period": week, "blocked_total": totaal,
            "top_rules": [dict(r) for r in top]}


def filtered_mails(limit: int = 50) -> List[dict]:
    """De weggehouden mail zélf, met het bewijs erbij. Dit is het pad dat
    'strenger filteren' verantwoord maakt (zelfde afweging als de bak
    'Uitgefilterd' bij de SEO-kansen): je kunt zien wat er weg is en het met één
    tik terughalen."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT e.id, e.subject, e.from_email, e.from_name, e.received_at, "
            "       e.triage_label, e.filter_reason, e.filter_rule_id, r.pattern "
            "FROM outlook_emails e LEFT JOIN mail_sender_rules r ON r.id = e.filter_rule_id "
            "WHERE e.filter_rule_id IS NOT NULL "
            "ORDER BY e.received_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Seed: de oude code-lijsten worden data ─────────────────────────────────

def seed_system_rules(conn=None) -> int:
    """Zet de geërfde `VENDOR_NOISE_*`-lijsten één keer om in regels.

    Waarom niet gewoon de lijsten laten staan: zolang ze in code stonden was
    "waarom zie ik deze mail niet?" onbeantwoordbaar zonder een editor, en kon
    niemand een te brede regel uitzetten. `weareimpact.nl` staat bewust NIET in
    de seed — dat is Vincents eigen bedrijfsdomein, en de mails die dat filter
    moest vangen kwamen in werkelijkheid uit een verkeerd gescope'te Graph-call
    (zie service.sync_inbox). Zijn eigen automatische mailingen worden gedekt
    door de smallere `noreply@`-regels.

    Idempotent: bestaande regels worden niet overschreven (een systeemregel die
    Vincent heeft uitgezet moet uit blijven).
    """
    from ..mail.classify import VENDOR_NOISE_DOMAINS, VENDOR_NOISE_SENDERS

    eigen_domein_uitzondering = {"weareimpact.nl"}
    patronen = {p.lower().strip() for p in VENDOR_NOISE_DOMAINS if p and p.strip()}
    patronen |= {p.lower().strip() for p in VENDOR_NOISE_SENDERS if p and p.strip()}
    patronen -= eigen_domein_uitzondering

    def _schrijf(c) -> int:
        n = 0
        for p in sorted(patronen):
            cur = c.execute(
                "INSERT OR IGNORE INTO mail_sender_rules "
                "(pattern, scope, action, reason, source, active, created_at) "
                "VALUES (?,?,?,?,'systeem',1,?)",
                (p, SCOPE_DEEL, ACTIE_GEEN_KLANT,
                 "standaardregel: webshop / vacaturesite / digest / systeemmelding",
                 _now()),
            )
            n += cur.rowcount or 0
        return n

    # De migratie geeft zijn eigen verbinding mee: die houdt de migratie-lock
    # vast, en een tweede get_conn() zou daarop wachten (zie _migrate_projectnamen).
    if conn is not None:
        return _schrijf(conn)
    with get_conn() as c:
        return _schrijf(c)
