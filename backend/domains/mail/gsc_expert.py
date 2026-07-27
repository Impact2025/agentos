"""GSC-expert agent — zet een Google Search Console notificatiemail om in een
concrete, uitvoerbare fix-gids (in de merkstem van het project), en LEEFT van
Vincents feedback zodat elke volgende analyse beter wordt.

Werkt los van de gewone helpdesk-drafter: die legt het probleem uit in
algemene termen, maar deze agent haalt de ÉCHTE situatie van de site op via
Search Console en vertaalt dat naar stappen die Vincent (of de klant zelf)
meteen kan uitvoeren.

Leer-loop (per domein + reden):
  * Elke analyse wordt opgeslagen in gsc_analyses (incl. of live GSC-data
    gebruikt werd en de confidence-score).
  * Vincent geeft feedback (1-5 sterren, en eventueel een verbeterde versie).
    Een verbeterde versie wordt de GOUDS
    TANDAARD voor dat domein+die reden: bij een volgende melding voor dezelfde
    combinatie neemt de agent die tekst als basis/voice-voorbeeld.
  * Lage gemiddelde scores verlagen de confidence → de agent speelt de melding
    ter review (menselijke klik) in plaats van 'm blind te verzenden.

Auto-actie (veilig):
  * De agent MAG automatisch verzenden/oplossen, MAAR alleen als:
      - de ontvanger een ÉCHT mens is (geen no-reply@ / google / sc-noreply),
        én
      - de confidence boven een drempel ligt (hoog genoeg zeker van de fix).
  * GSC-notificaties komen van sc-noreply@google.com en gaan daarmee naar
    niémand — die worden automatisch 'resolved' (geanalyseerd + vastgelegd)
    zónder dat er een mail naar Google wordt gestuurd. Dat is de enige
    correcte actie: je kunt Google niet 'antwoorden' op een no-reply-adres.
"""
import logging
import re
import uuid
from typing import Dict, List, Optional, Tuple

import httpx

from ...shared.config import (
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
)
from ...shared.database import get_conn

logger = logging.getLogger(__name__)

# Google stuurt dit adres voor Search Console-transactiemails.
GSC_SENDER = "sc-noreply@google.com"

# Adressen waarvoor auto-verzenden NOOIT mag (notificaties zonder mens).
_AUTO_SEND_BLOCKLIST_DOMAINS = (
    "google.com", "googlemail.com", "noreply", "no-reply", "do-not-reply",
    "mailer-daemon", "sc-noreply", "search-console", "webmaster",
)

# Boven deze confidence handelt de agent autonoom (verzenden of oplossen),
# daaronder speelt hij de melding ter review (menselijke klik).
AUTO_CONFIDENCE_THRESHOLD = 0.8

# Bekende GSC-indexeringsredenen (Nederlandse en Engelse varianten) →
# gestructureerde uitleg + typische oorzaak. De parser zoekt op trefwoorden
# in onderwerp + body; de LLM krijgt de herkende reden mee als context.
_REASON_PATTERNS = [
    ("omleiding", (
        "Pagina met omleiding",
        "Er staat een redirect (301/302) op een pagina die in de sitemap staat. "
        "Google volgt de redirect en indexeert de bestemming, niet de originele "
        "URL — dat geeft dubbele-contentrisico's en verwarring over welke URL "
        "'de' pagina is."
    )),
    ("canonieke", (
        "Dubbele pagina zonder door de gebruiker geselecteerde canonieke versie",
        "Google ziet twee of meer pagina's die sterk op elkaar lijken (bv. "
        "www/non-www, trailing slash, ?ref= of /index). Geen ervan heeft een "
        "heldere rel=\"canonical\" die aangeeft welke de voorkeursversie is."
    )),
    ("noindex", (
        "Beveiligd met noindex",
        "De pagina draagt een noindex- (of x-robots-tag met noindex) mee, of "
        "staat achter een login/betaalmuur, waardoor Google hem niet mag "
        "indexeren. Vaak per ongeluk op een pagina die wél zichtbaar hoort te "
        "zijn."
    )),
    ("robots.txt", (
        "Geblokkeerd door robots.txt",
        "robots.txt weigert toegang tot (een deel van) de pagina, dus Google "
        "kan hem niet crawlen en indexeren."
    )),
    ("uitgesloten", (
        "Door de eigenaar uitgesloten",
        "Er is expres of per ongeluk een uitsluiting ingesteld (noindex, "
        "canonical naar een andere pagina, of een crawl-delay/blokkade)."
    )),
    ("dubbele pagina", (
        "Dubbele pagina",
        "De pagina is bijna identiek aan een andere pagina op dezelfde site. "
        "Google kiest er één uit en laat de rest weg uit de index."
    )),
    ("404", (
        "Pagina niet gevonden (404)",
        "De URL verwijst naar een pagina die niet (meer) bestaat. Google "
        "verwijdert 404's uit de index en ze verliezen hun ranking."
    )),
    ("zachte 404", (
        "Zachte 404",
        "De server geeft een '200 OK' terug voor een pagina die eigenlijk "
        "leeg/nietszeggend is — Google ziet dat als een 404 en indexeert niet."
    )),
    ("autorisatie", (
        "Vereist autorisatie",
        "De pagina staat achter een login of wachtwoord; Googlebot komt er niet "
        "in en indexeert hem niet."
    )),
    ("gecrawld", (
        "Kan niet worden gecrawld",
        "Googlebot kan de pagina technisch niet bereiken (serverfout, time-out, "
        "DNS, of een blokkade)."
    )),
]


def is_gsc_mail(from_addr: str, subject: str = "", body: str = "") -> bool:
    """Herkent een Search Console-transactiemail aan afzender óf kenmerkende
    onderwerpen/body. Ruimer dan alleen de afzender, zodat ook doorgestuurde
    meldingen (van een klant) als GSC-mail worden gezien."""
    f = (from_addr or "").lower()
    if GSC_SENDER in f:
        return True
    text = ((subject or "") + " " + (body or "")).lower()
    if "search console" in text and (
        "niet" in text and ("geïndexeerd" in text or "geindexeerd" in text)
    ):
        return True
    return False


def _is_auto_sendable(to_addr: str) -> bool:
    """Mag de agent deze melding automatisch verzenden? Alleen naar een écht
    mens-adres. No-reply / google / sc-noreply → nooit (daar kun je niet
    antwoorden en het zou de mailbox brandmerken)."""
    a = (to_addr or "").lower()
    if "@" not in a:
        return False
    domain = a.split("@", 1)[1]
    if any(b in a or b in domain for b in _AUTO_SEND_BLOCKLIST_DOMAINS):
        return False
    return True


def detect_reason(subject: str, body: str) -> Tuple[str, str]:
    """Geef (reden-titel, uitleg) terug op basis van trefwoorden in de mail.
    Valt terug op een algemene 'onbekende indexeringsreden' als niets matcht."""
    text = ((subject or "") + " " + (body or "")).lower()
    for key, (title, explanation) in _REASON_PATTERNS:
        if key in text:
            return title, explanation
    return (
        "Onbekende nieuwe indexeringsreden",
        "Google meldt een nieuwe reden waarom pagina's niet geïndexeerd worden, "
        "maar de precieze reden is niet uit de mail af te leiden. Check het "
        "Indexeringsrapport in Search Console voor de exacte melding.",
    )


def _extract_site(subject: str) -> Optional[str]:
    """Haal de site-naam uit de GSC-onderwerpregel, bv.
    '... op de site steentjebijsteentje.nl' → 'steentjebijsteentje.nl'."""
    m = re.search(r"op de site\s+([^\s]+)", (subject or ""), re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(".").lower()
    m2 = re.search(r"([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)", (subject or ""), re.IGNORECASE)
    return m2.group(1).lower() if m2 else None


def _site_for_domain(conn, domain: str) -> Optional[Dict]:
    """Match een domein (bv. 'skillkaart.nl') op een site via base_url of
    gsc_property. Genormaliseerd, net als de rest van de app."""
    domain = (domain or "").lower().replace("sc-domain:", "").strip()
    if not domain:
        return None
    rows = conn.execute("SELECT * FROM sites").fetchall()
    for r in rows:
        r = dict(r)
        base = (r.get("base_url") or "").lower().replace("https://", "").replace("http://", "").rstrip("/")
        gsc = (r.get("gsc_property") or "").lower().replace("sc-domain:", "")
        if domain in (base, gsc) or base.endswith(domain) or domain.endswith(base.split("/")[-1] if base else ""):
            return r
    for r in rows:
        r = dict(r)
        if domain.split(".")[0] and domain.split(".")[0] in (r.get("name") or "").lower():
            return r
    return None


# ── Leer-laag: feedback + verbeterde goudstandaarden ─────────────────────────
def _fetch_learning(conn, domain: str, reason: str) -> Dict:
    """Lees wat we weten over (domein, reden): gemiddelde score, aantal
    feedback-rijen, en de beste verbeterde gids (goudstandaard) als die er is.

    Retourneert een dict die de LLM bij de volgende analyse meekrijgt zodat de
    agent de toon/aanpak van Vincents verbeteringen imiteert."""
    out = {"avg_score": None, "feedback_n": 0, "golden_text": "", "notes": []}
    # Goudstandaard: de hoogst-beoordeelde verbeterde versie voor deze combi.
    gold = conn.execute(
        "SELECT corrected_text, score FROM gsc_feedback "
        "WHERE domain=? AND reason=? AND corrected_text != '' "
        "ORDER BY score DESC, created_at DESC LIMIT 1",
        (domain, reason),
    ).fetchone()
    if gold:
        out["golden_text"] = gold["corrected_text"]
        out["avg_score"] = gold["score"]
        out["feedback_n"] = 1
        return out
    # Anders: gemiddelde score + notities uit eerdere feedback.
    rows = conn.execute(
        "SELECT score, note FROM gsc_feedback WHERE domain=? AND reason=? "
        "AND score > 0 ORDER BY created_at DESC LIMIT 5",
        (domain, reason),
    ).fetchall()
    if rows:
        scores = [r["score"] for r in rows if r["score"]]
        out["avg_score"] = round(sum(scores) / len(scores), 2) if scores else None
        out["feedback_n"] = len(rows)
        out["notes"] = [r["note"] for r in rows if r["note"]]
    return out


def record_feedback(analysis_id: str, domain: str, reason: str,
                    score: int = 0, corrected_text: str = "", note: str = "") -> Dict:
    """Sla Vincents feedback op. Een verbeterde tekst (corrected_text) wordt de
    goudstandaard voor toekomstige meldingen van dit domein+die reden."""
    aid = analysis_id or ("fb_" + uuid.uuid4().hex[:12])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO gsc_feedback(id, analysis_id, domain, reason, score, "
            "corrected_text, note) VALUES(?,?,?,?,?,?,?)",
            ("fb_" + uuid.uuid4().hex[:12], aid, domain, reason,
             int(score or 0), corrected_text or "", note or ""),
        )
    return {"ok": True, "analysis_id": aid, "domain": domain, "reason": reason}


# ── LLM-call (zelfde backend/fallback-gedrag als de helpdesk-drafter) ────────
_SYSTEM = (
    "Je bent de SEO/Search Console-expert van {brand}. Vincent van Munster "
    "(WeAreImpact) stuurt namens zijn klanten dit soort meldingen door naar "
    "zichzelf, zodat hij ze snel en vakkundig kan beantwoorden.\n"
    "Schrijf een concreet, uitvoerbaar antwoord in het Nederlands (of de taal "
    "van de klant), in de eerste persoon, warm maar vakbekwaam — geen "
    "robottaal. Geen uitroeptekens-geweld.\n"
    "Structuur van je antwoord:\n"
    "1. Korte erkenning van de melding (1 zin).\n"
    "2. Wat er aan de hand is — leg de GSC-reden uit in gewone taal, gekoppeld "
    "aan DIT domein.\n"
    "3. De concrete fix — 3-5 genummerde stappen die Vincent (of de klant) kan "
    "uitvoeren. Noem waar mogelijk echte URL's/pagina's uit de live GSC-data.\n"
    "4. Wat wij kunnen doen — bied concreet aan (bv. canonical-tag zetten, "
    "redirect-loops oplossen, sitemap opschonen) in de ik-vorm.\n"
    "Maximaal 220 woorden. Verzin géén garanties of feiten die niet in de "
    "meegeleverde GSC-data staan. Als de live-data leeg is, zeg dan eerlijk "
    "dat we de specifieke pagina's in Search Console moeten opzoeken.\n"
)


def _sync_openmodel(system: str, user: str) -> str:
    url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
    payload = {
        "model": OPENMODEL_MODEL or "deepseek-v4-flash",
        "max_tokens": 1100,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            url,
            headers={
                "x-api-key": OPENMODEL_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    if "content" in data:
        return "".join(p.get("text", "") for p in data["content"] if p.get("type") == "text")
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return data.get("text", "")


def _draft_with_llm(brand: str, reason_title: str, reason_expl: str,
                    site_name: str, domain: str, gsc_context: str,
                    lang: str, learning: Dict) -> str:
    lang_note = "Antwoord in het Nederlands." if lang != "en" else "Reply in English."
    system = _SYSTEM.format(brand=brand) + "\n\n" + lang_note
    user_parts = [
        f"Domein: {domain} ({site_name or 'onbekend'})",
        f"GSC-reden: {reason_title}",
        f"Uitleg van de reden: {reason_expl}",
        f"LIVE SEARCH CONSOLE-DATA voor dit domein:\n{gsc_context}",
    ]
    # Leer-laag: imiteer Vincents eerdere verbeteringen voor deze combi.
    if learning.get("golden_text"):
        user_parts.append(
            "LEERVOORBEELD — zo heeft Vincent deze melding eerder verbeterd tot "
            "de definitieve versie (imiter de toon, diepgang en structuur):\n"
            + learning["golden_text"]
        )
    elif learning.get("notes"):
        user_parts.append(
            "Eerdere feedback van Vincent op dit type melding: "
            + "; ".join(learning["notes"])
        )
    user_parts.append("Schrijf de fix-gids hierboven volgens de structuur.")
    user = "\n\n".join(user_parts)

    if not OPENMODEL_API_KEY:
        return _fallback_text(domain, reason_expl)
    try:
        return _sync_openmodel(system, user).strip()
    except Exception as e:
        logger.warning("GSC-expert LLM mislukt: %s", e)
        return _fallback_text(domain, reason_expl)


def _fallback_text(domain: str, reason_expl: str) -> str:
    return (
        f"Hoi, dank voor de Search Console-melding over {domain}.\n\n"
        f"Wat er speelt: {reason_expl}\n\n"
        "Ik kijk de live indexeringsdata erop na en stuur je een concrete "
        "stappenlijst — maar je kunt zelf alvast in Search Console → "
        "Indexeringsrapport zien welke specifieke URL's het betreft. "
        "Zodra ik de pagina's heb geïdentificeerd, los ik de oorzaak aan "
        "(canonical/redirect/sitemap) en laat ik het her-crawlen.\n\n"
        "Groet, Vincent"
    )


def _build_gsc_context(domain: str, gsc_property: str) -> str:
    """Haal live GSC-data op voor het domein. Geef een leesbare samenvatting of
    een eerlijke 'geen data'-melding terug. Nooit verzonnen."""
    try:
        from ...domains.seo import gsc as gsc_mod
        if not gsc_mod.is_configured() or not gsc_property:
            return "(Geen Search Console gekoppeld voor dit domein — controleer het Indexeringsrapport in Search Console.)"
        pages = gsc_mod.fetch_page_performance(gsc_property, days=28, row_limit=10)
        queries = gsc_mod.fetch_query_performance(gsc_property, days=90, row_limit=10)
        if not pages and not queries:
            return "(Geen recente GSC-data gevonden voor dit domein — mogelijk nieuw of weinig verkeer. Check het Indexeringsrapport voor de specifieke URL's.)"
        lines = []
        if pages:
            lines.append("Top-pagina's (impressies / gem. positie):")
            for p in pages[:8]:
                lines.append(f"  - {p['page']}  ({p['impressions']} impr, pos {p['position']})")
        if queries:
            lines.append("Belangrijkste zoekwoorden:")
            for q in queries[:8]:
                lines.append(f"  - \"{q['query']}\"  ({q['impressions']} impr, pos {q['position']}, CTR {q['ctr']}%)")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("GSC-data ophalen mislukt voor %s: %s", domain, e)
        return f"(Kon GSC-data niet ophalen: {e}. Check het Indexeringsrapport in Search Console.)"


def _estimate_confidence(used_live_gsc: bool, reason_title: str,
                         learning: Dict, auto_sendable: bool) -> float:
    """0..1 zekerheid dat de gegenereerde fix-gids volledig en correct is.

    Hoger als: we live GSC-data hadden, de reden bekend is, en eerdere feedback
    voor deze combi goed scoort. Lager als geen live data of matige feedback.
    Deze score bepaalt of de agent autonoom mag handelen."""
    score = 0.5
    if used_live_gsc:
        score += 0.2
    if reason_title and "Onbekende" not in reason_title:
        score += 0.15
    if auto_sendable:
        score += 0.05  # een mens kan altijd terugschrijven als het scheef zit
    avg = learning.get("avg_score")
    if avg is not None:
        if avg >= 4:
            score += 0.15
        elif avg <= 2:
            score -= 0.25
    return round(min(max(score, 0.0), 1.0), 2)


def analyze_and_fix(reply_id: int, auto: bool = True,
                    send_fn=None) -> Optional[Dict]:
    """Hoofdentry: parseer de GSC-mail, haal live data op, schrijf een
    fix-gids terug in mail_reply, leg de analyse vast in de leer-laag, en
    handel veilig af (verzenden óf oplossen óf ter review).

    auto=True  → de agent mag zelfstandig verzenden/oplossen wanneer de
                 confidence hoog genoeg is en de ontvanger een écht mens is.
    auto=False → alleen analyseren + ter review zetten (handmatige knop).

    send_fn   → injecteerbare verzendfunctie (service.send_reply) zodat we
                 hier niet circular inimporten. Bij None wordt nooit verzonden.

    Retourneert de analyse-metadata, of None als het concept ontbreekt / geen
    GSC-mail is.
    """
    with get_conn() as conn:
        r = conn.execute(
            "SELECT r.id, r.to_addr, r.subject, r.draft_body, r.status, "
            "r.mailbox_id, i.body_text AS mail_body, i.from_addr, i.subject AS inbox_subject, "
            "m.project "
            "FROM mail_reply r "
            "JOIN mailboxes m ON m.id=r.mailbox_id "
            "JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.id=?",
            (reply_id,),
        ).fetchone()
        if not r:
            return None
        r = dict(r)
        from_addr = r["from_addr"] or ""
        # Gebruik de ORIGINELE inbox-subject voor parsing (die bevat "op de site
        # X"); de reply-subject is "Re: ..." en kan de domeinregel missen.
        subject = r.get("inbox_subject") or r["subject"] or ""
        mail_body = r["mail_body"] or ""
        if not is_gsc_mail(from_addr, subject, mail_body):
            return None

        brand = r["project"] or "dit project"
        reason_title, reason_expl = detect_reason(subject, mail_body)
        domain = _extract_site(subject) or (from_addr if "google" not in from_addr else "")
        site = _site_for_domain(conn, domain) if domain else None
        gsc_property = (site or {}).get("gsc_property", "") if site else ""
        site_name = (site or {}).get("name", "") if site else ""

        gsc_context = _build_gsc_context(domain, gsc_property)
        used_live = ("Geen Search Console" not in gsc_context
                     and "Kon GSC-data niet" not in gsc_context
                     and "gekoppel" not in gsc_context)
        lang = "en" if ("english" in mail_body.lower() or "hello" in mail_body.lower()) else "nl"
        auto_sendable = _is_auto_sendable(r["to_addr"])

        # Leer-laag: wat weten we van (domein, reden)?
        learning = _fetch_learning(conn, domain, reason_title) if domain else {}

        analysis = _draft_with_llm(
            brand=brand, reason_title=reason_title, reason_expl=reason_expl,
            site_name=site_name, domain=domain, gsc_context=gsc_context,
            lang=lang, learning=learning,
        )

        confidence = _estimate_confidence(used_live, reason_title, learning, auto_sendable)

        # Beslis disposition
        disposition = "review"
        auto_sent = 0
        if auto:
            if auto_sendable and confidence >= AUTO_CONFIDENCE_THRESHOLD and send_fn:
                # Echt mens + hoog vertrouwen → versturen.
                try:
                    if send_fn(reply_id):
                        disposition = "sent"
                        auto_sent = 1
                    else:
                        disposition = "review"
                except Exception as e:
                    logger.warning("GSC auto-verzenden mislukt voor %s: %s", reply_id, e)
                    disposition = "review"
            elif not auto_sendable:
                # Notificatie zonder mens (bv. sc-noreply@google.com): niet
                # verzenden, wél oplossen (vastleggen + ter kennisgeving).
                disposition = "resolved"
            else:
                disposition = "review"

        # Schrijf de fix-gids terug in het concept (edited_body).
        new_status = "edited" if disposition in ("review", "sent") else "gsc_resolved"
        conn.execute(
            "UPDATE mail_reply SET draft_body=?, edited_body=?, status=?, "
            "gsc_status=?, gsc_confidence=?, gsc_fixed_by=?, gsc_analysis_id=? "
            "WHERE id=?",
            (analysis, analysis, new_status, "resolved",
             confidence, ("agent" if disposition in ("sent", "resolved") else ""),
             "", reply_id),
        )

        # Leer-laag: analyse vastleggen.
        analysis_id = "gsc_" + uuid.uuid4().hex[:12]
        conn.execute(
            "UPDATE mail_reply SET gsc_analysis_id=? WHERE id=?",
            (analysis_id, reply_id),
        )
        conn.execute(
            "INSERT INTO gsc_analyses(id, domain, site_name, reason, "
            "used_live_gsc, analysis, confidence, disposition, auto_sent) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (analysis_id, domain, site_name, reason_title, 1 if used_live else 0,
             analysis, confidence, disposition, auto_sent),
        )

        return {
            "reply_id": reply_id,
            "domain": domain,
            "site": site_name,
            "reason": reason_title,
            "used_live_gsc": used_live,
            "confidence": confidence,
            "disposition": disposition,
            "auto_sent": bool(auto_sent),
            "analysis": analysis,
        }
