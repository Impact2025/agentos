"""Classificeer een inkomende mail naar type, zodat we alleen échte vragen
naar een concept-antwoord sturen — en afspraak-verzoeken naar de agenda-agent.

Wereldklasse-principe: liever één vraag missen dan tien spam-concepten
aanmaken die Vincent met de hand moet weggooien. De oude classifier zag
elke mail met een "?" of het woord "help" als 'question' — waardoor
crypto-invites (autotrading.vip), USDT-winacties (mexc.com) en Samsung-promo's
allemaal een concept kregen. Dat is erger dan de converse: die blijven in
mail_inbox staan (gelabeld) en komen nooit als concept terug.

Categorieën:
  spam       — crypto/scam/promo/marketing met commerciële bedoeling
  newsletter — nieuwsbrieven / automatische updates (geen antwoord nodig)
  invoice    — factuur / betaling (zichtbaar, niet auto-antwoorden)
  appointment— expliciet verzoek om een afspraak / gesprek / belafspraak
  question   — een échte vraag van een mens (client, interim, leverancier)
  other      — rest
"""
import re

# ── Spam / scam / promo (harde signalen) ────────────────────────────────────
# Dit zijn domein- en merkpatronen die bijna altijd ongewenst zijn. De
# classifier weegt deze zwaarder dan de vraag-signalen: zelfs met een "?" in
# de body blijft crypto-promo spam.
SPAM_SENDER_DOMAINS = (
    "autotrading.vip", "autotrading", "mexc.com", "binance", "coinbase",
    "crypto", "usdt", "blockchain", "nofollow", "notify.railway.app",
    "notify", "vercel.app", "vercel.com", "github.com", "notifications@github",
    "beursgenoten.nl", "bewaardvoorjou", "pootgelukkig", "vrijwilligersassistent",
    "samsung.com", "email.samsung.com", "m1.email.samsung", "samsungmobile",
    "dhgate.com", "e3.dhgate.com", "ali", "wish.com", "shein", "temu",
    "marketing", "newsletter", "noreply", "no-reply", "notify", "do-not-reply",
)
SPAM_SUBJECT_HINTS = (
    "win up to", "win up", "usdt", "btc", "crypto", "invitation to win",
    "you're invited to win", "unpacked", "exclusive offer", "limited offer",
    "50% off", "korting", "actie", "gratis", "prijs", "jackpot", "bonus",
    "final hours", "laatste kans", "nog één dag", "claim your", "claim je",
    "payment link", "verify your", "bevestig je", "account verification",
    "reset your password", "wachtwoord reset", "security alert",
)
SPAM_BODY_HINTS = (
    "usdt", "btc", "ethereum", "crypto exchange", "trading signals",
    "double your", "guaranteed profit", "investment opportunity",
    "unsubscribe", "view in browser", "you received this email because",
    "you are receiving this", "email preferences", "manage your subscription",
    "this email was sent to", "copyright", "all rights reserved",
    "promotional", "advertisement", "reklama", "offer ends",
)

# ── Newsletters / automatische updates (geen antwoord) ──────────────────────
NEWSLETTER_HINTS = (
    "unsubscribe", "newsletter", "web push", "built for developers",
    "view in browser", "five ways to", "getting started", "dagelijks",
    "wekelijks", "maandelijks", "onze nieuwsbrief", "je abonnement",
    "deze mail is automatisch", "automatisch gegenereerd",
)
# System/CI/notifications: deployment-fails, build-fails, reminders.
SYSTEM_SENDER_DOMAINS = (
    "notify.railway.app", "vercel.com", "github.com", "notifications@github",
    "noreply", "no-reply", "do-not-reply", "no_reply", "mailer-daemon",
    "postmaster", "bounce", "automated", "system", "alerts@",
)
SYSTEM_SUBJECT_HINTS = (
    "build failed", "build succeeded", "deployment", "ci ", "pipeline",
    "run failed", "run succeeded", "cron", "monitoring", "uptime",
    "disk space", "ssl", "certificate", "renewal", "reminder",
    "inloglink", "inlog link", "magic link", "verificatielink",
    "your otp", "verificatiecode", "code is", "login code",
)

# ── Facturen / betalingen ───────────────────────────────────────────────────
INVOICE_HINTS = ("factuur", "invoice", "btw", "betaal", "aanmaning", "betaling",
                 "overmaking", "incasso", "nota", "rekening")

# ── Afspraak-verzoeken (route naar agenda-agent) ──────────────────────────
APPOINTMENT_HINTS = (
    "afspraak", "afspreken", "kunnen we bellen", "bellen", "belafspraak",
    "gesprek", "ontmoeting", "inzage", "kennismaking", "introgesprek",
    "plan een", "inplannen", "beschikbaar", "beschikbaarheid", "agenda",
    "wanneer past", "wanneer schikt", "voorstel voor", "uitnodiging",
    "invitation", "schedule", "meeting", "call", "walkthrough", "demo",
    "videocall", "zoom", "teams", "google meet", "past het je", "schikken",
)
# Sterke afspraak-markeringen: als een van deze én een tijd/dag genoemd wordt,
# is het vrijwel zeker een afspraak-verzoek.
APPOINTMENT_STRONG = (
    "kunnen we", "zullen we", "ik stel voor", "ik plan", "ik stel een gesprek voor",
    "zou het lukken", "hebben we", "zullen we bellen", "een belafspraak",
    "een gesprek inplannen", "wanneer kunnen we", "wanneer hebben we",
)

# ── Échte vraag-signalen (menselijke inhoud) ───────────────────────────────
QUESTION_HINTS = (
    "?", "hoe", "wat", "kan ik", "kunt u", "kunnen jullie", "help", "vraag",
    "probleem", "werkt niet", "niet meer", "reset", "wachtwoord", "inloggen",
    "account", "bestelling", "levering", "error", "fout", "waarom", "wanneer",
    "advies", "hoeft", "klopt", "klopt dit", "bevestig", "bevestigen",
)


def _sender_domain(from_addr: str) -> str:
    if not from_addr:
        return ""
    m = re.search(r"@([^>)\s]+)", from_addr)
    return (m.group(1) if m else from_addr).lower()


def _count_hits(text: str, hints) -> int:
    return sum(1 for h in hints if h in text)


def classify(subject: str, body: str, from_addr: str = "") -> str:
    s = (subject or "").lower()
    b = (body or "").lower()
    frm = (from_addr or "").lower()
    dom = _sender_domain(from_addr)

    # 1) Harde spam: verdachte domeinen of crypto/promo-patronen.
    #    Deze winnen altijd van vraag-signalen (geen "?"-exceptie).
    if any(d in dom for d in SPAM_SENDER_DOMAINS if d) and not _is_trusted(dom):
        return "spam"
    if _count_hits(s, SPAM_SUBJECT_HINTS) >= 1 or _count_hits(b, SPAM_BODY_HINTS) >= 2:
        return "spam"

    # 2) Automatische system/meldingen (CI, reminders, OT-grubby). Geen antwoord.
    if any(d in dom for d in SYSTEM_SENDER_DOMAINS if d) or _count_hits(s, SYSTEM_SUBJECT_HINTS) >= 1:
        return "other"  # blijft zichtbaar als 'other', niet als concept

    # 3) Newsletters / promo-updates.
    if _count_hits(s, NEWSLETTER_HINTS) >= 1 or _count_hits(b, NEWSLETTER_HINTS) >= 2:
        return "newsletter"

    # 4) Facturen / betalingen.
    if _count_hits(s, INVOICE_HINTS) >= 1 or _count_hits(b, INVOICE_HINTS) >= 2:
        return "invoice"

    # 5) Afspraak-verzoek → agenda-agent. Sterk signaal volstaat alleen;
    #    zwak signaal + dag/tijd-context ook.
    if _count_hits(s + " " + b, APPOINTMENT_STRONG) >= 1:
        return "appointment"
    if _count_hits(s + " " + b, APPOINTMENT_HINTS) >= 2:
        return "appointment"
    if _count_hits(s + " " + b, APPOINTMENT_HINTS) >= 1 and _has_time_context(s + " " + b):
        return "appointment"

    # 6) Échte vraag — pas ná alle bovenstaande filters, en vereis méér dan
    #    alleen een "?" (die zit ook in promo's). Minimaal 1 sterk vraag-woord
    #    OF ("?" + een werkwoord-achtig signaal).
    q_hits = _count_hits(s + " " + b, QUESTION_HINTS)
    if q_hits >= 2:
        return "question"
    if "?" in (s + b) and q_hits >= 1:
        return "question"

    return "other"


def _is_trusted(domain: str) -> bool:
    """Domeinen die we nooit als spam zien, ondanks een 'verdacht' substring.

    bv. 'notify' zit in SPAM_SENDER_DOMAINS maar ook in legitieme
    notificaties; we vertrouwen hier op expliciete whitelist.
    """
    trusted = (
        "weareimpact.nl", "bewaardvoorjou.nl", "skillkaart.nl", "bijeen.app",
        "ictusgo.nl", "movimento-zorg.nl", "interim", "_overheid", "belasting",
        "kvk.nl", "kamer.van.koophandel", "rabobank", "ing.nl", "abnamro",
        "microsoft.com", "outlook.com", "live.com", "office365", "google.com",
        "gmail.com", "linkedin.com", "xing", "novi", "huisarts", "zorg",
    )
    return any(t in domain for t in trusted)


_DAYS = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag",
         "zondag", "morgen", "overmorgen", "volgende week", "komende week",
         "vandaag", "vanmiddag", "vanavond", "ochtend", "middag", "avond")
_TIME = (r"\d{1,2}[:.]\d{2}", r"\d{1,2}\s?uur", r"\b\d{1,2}\b")


def _has_time_context(text: str) -> bool:
    """Heeft de tekst een dag- en/of tijdaanduiding? (voor afspraak-detectie)"""
    if any(d in text for d in _DAYS):
        return True
    if re.search(r"\d{1,2}[:.]\d{2}", text):
        return True
    if re.search(r"\d{1,2}\s?uur", text):
        return True
    return False
