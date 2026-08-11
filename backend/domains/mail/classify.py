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

from . import bulk as bulk_mod
from ...shared.mail_text import strip_noise

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
    "marketing", "newsletter", "nieuwsbrief", "noreply", "no-reply", "notify", "do-not-reply",
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
    "jobalert", "indeed.com",
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
    "zou het lukken", "zullen we bellen", "een belafspraak",
    "een gesprek inplannen", "wanneer kunnen we", "wanneer hebben we",
)
# Losse "hebben we" (zonder 'wanneer' ervoor) stond hier ooit ook, maar is de
# gewoonste Nederlandse toekomstige-tijd-constructie die er is — "hebben we
# alvast een beter beeld van jullie vraag" is geen afspraakwens. Gemeten
# 10 aug 2026: precies die zin in een gewone antwoordmail leverde een
# afspraakvoorstel op. 'wanneer hebben we' blijft wél STRONG: dát vraagt
# expliciet om een moment.
# Een afspraak AFZEGGEN gebruikt dezelfde woorden ('afspraak', 'teams',
# 'kennismaking') als een verzoek — zonder deze check wint 'afspraak' altijd
# en maakt de agenda-agent een voorstel voor een moment dat juist niet
# doorgaat (gemeten 9 aug 2026: "we gaan er derhalve van uit dat de Teams
# afspraak ... geen doorgang kan vinden" leverde een voorstel op mét tijd).
CANCEL_HINTS = (
    "geen doorgang", "gaat niet door", "kan niet doorgaan", "afgezegd",
    "afzeggen", "geannuleerd", "annuleren", "niet meer nodig",
    "trekken we in", "stellen we uit", "uitgesteld", "helaas geen reactie",
)

# ── Échte vraag-signalen (menselijke inhoud) ───────────────────────────────
QUESTION_HINTS = (
    "?", "hoe", "wat", "kan ik", "kunt u", "kunnen jullie", "help", "vraag",
    "probleem", "werkt niet", "niet meer", "reset", "wachtwoord", "inloggen",
    "account", "bestelling", "levering", "error", "fout", "waarom", "wanneer",
    "advies", "hoeft", "klopt", "klopt dit", "bevestig", "bevestigen",
)


# ── Vendor / geen-potentiële-klant ruis (direct archief) ──────────────────
# Webshops, marktplaatsen, vacature-platforms, deal-sites en retailer-promo's
# zijn nooit een potentiële klant. Vincent wil die niet in de inbox zien.
# Substring-match op het volledige afzenderadres (niet alleen domein) zodat
# 'info@aliexpress.com' en 'promo@aliexpress-mail.nl' allebei raken.
VENDOR_NOISE_DOMAINS = (
    # Webshops / marketplaces / deals
    "aliexpress", "aliexpress", "amazon", "bol.com", "bolcom", "wish.com",
    "shein", "temu", "dhgate", "ibood", "zalando", "wehkamp", "coolblue",
    "coolblue.nl", "mediamarkt", "bcc.nl", "fonq", "beslist", "marktplaats",
    "shop-canda", "canda", "aboutyou", "otto", "hEMA", "hema.nl", "action",
    "xelebra", "trendshopping", "shopping", "shop.", "store.",
    # Vacature / recruitment-platforms (geen klant, wel kandidatenstroom)
    "indeed", "nationalevacaturebank", "vacaturebank", "vacatures", "jobbird",
    "werkzoeken", "monsterboard", "stepstone", "randstad", "tempo-team",
    "tempoteam", "uitzend", "recruit", "jobmail", "kandidaten@",
    # Social / community digests
    "facebookmail.com", "friendupdates", "linkedin.com/e/", "skool.com",
    "community", "digest",
    # Systeemnotificaties / rapportages (DMARC, CI, monitoring)
    "dmarcreport", "dmarc", "getsentry.com", "sentry", "neon.tech",
    "noreply", "no-reply", "mailer-daemon", "postmaster", "bounce",
    "googlealerts", "googlealerts-noreply",
)
# Losse afzenders die wél op naam-mailen maar géén klant zijn (eigen
# automatische mailingen, nieuwsbrieven van eigen projecten). Worden herkend
# op (sub)string in het adres.
VENDOR_NOISE_SENDERS = (
    "shop-canda.com", "bewaardvoorjou.nl", "weareimpact.nl",
)


def is_inbox_noise(from_addr: str = "", subject: str = "", body: str = "") -> bool:
    """True als de mail géén potentiële klant is en direct gearchiveerd mag
    worden: webshops, marktplaatsen, vacature-sites, deal-promo's, social/
    community digests, systeemrapportages en eigen geautomatiseerde mailingen.

    Whistelisted vertrouwde domeinen (zie _is_trusted) blijven overeind — zo
    glipt een echte klant die toevallig 'shop' in zijn domein heeft er niet uit.
    """
    frm = (from_addr or "").lower()
    if not frm:
        return False
    # Google Alerts / vergelijkbare geautomatiseerde digest van een anderszins
    # vertrouwd domein (google.com) zijn wél noise — die mogen de trusted-check
    # niet overschrijven.
    if "googlealerts" in frm:
        return True
    if _is_trusted(_sender_domain(from_addr)):
        return False
    if any(d in frm for d in VENDOR_NOISE_DOMAINS if d):
        return True
    # Eigen geautomatiseerde mailingen (v.munster@weareimpact.nl ochtendritueel
    # etc.) — herkenbaar aan de bekende project-domeinen in het adres.
    if any(s in frm for s in VENDOR_NOISE_SENDERS if s):
        return True
    return False


def _sender_domain(from_addr: str) -> str:
    if not from_addr:
        return ""
    m = re.search(r"@([^>)\\s]+)", from_addr)
    return (m.group(1) if m else from_addr).lower()


def _count_hits(text: str, hints) -> int:
    """Losse sub-string-telling — bewust behouden voor de spam/nieuwsbrief/
    systeem-lijsten. Daar is over-matchen de veilige kant op: de mail wordt
    gelabeld en blijft staan, er vertrekt niets. Voor vraag- en afspraak-
    signalen geldt het omgekeerde, en daar gebruiken we `_count_words`."""
    return sum(1 for h in hints if h in text)


def _count_words(text: str, hints) -> int:
    """Woordgrens-telling voor de signalen die wél iets in gang zetten.

    'wat' mag niet matchen binnen 'watersport' en 'hoe' niet binnen
    'schoenen'; met sub-string-tellen haalde élke marketingmail van 2000
    tekens moeiteloos twee vraag-hits, en elk artikel van 7000 tekens de
    afspraak-marker 'hebben we' (1 aug 2026)."""
    aantal, _ = bulk_mod.count_words(text, hints)
    return aantal


def classify(subject: str, body: str, from_addr: str = "", headers=None) -> str:
    """Bepaal het type van een inkomende mail.

    `headers` is optioneel maar zwaarwegend: bevat de mail List-Unsubscribe of
    Precedence: bulk, dan is het een verzending en kan het per definitie geen
    persoonlijke vraag of afspraak-verzoek zijn. Zonder headers valt de
    beoordeling terug op de tekst-heuristiek in `bulk.bulk_reason`.
    """
    s = (subject or "").lower()
    b = (body or "").lower()
    frm = (from_addr or "").lower()
    dom = _sender_domain(from_addr)
    # Afspraak- en afzeggingssignalen mogen alleen uit wat de afzender NU
    # schrijft komen — niet uit een geciteerde oudere mail (bv. onze eigen
    # outreach die in het antwoord wordt meegestuurd) en niet uit de
    # handtekening/footer. Een boekingslink als "📆 Boek een afspraak in mijn
    # agenda" onder een naam+functie is marketing-boilerplate van de
    # afzender, geen verzoek aan ons (gemeten 10 aug 2026, nlvoorelkaar.nl:
    # een simpele afwijzing werd zo een afspraakvoorstel).
    b_clean = strip_noise(body or "").lower()

    # 0) Bulk wint van alles wat een antwoord of afspraak zou opleveren. Een
    #    mailing beantwoord je niet, hoe vriendelijk de vraagzin ook klinkt.
    #    Deze check staat vóór de spam-check zodat een legitieme nieuwsbrief
    #    ook als nieuwsbrief in het overzicht komt en niet als 'spam'.
    if bulk_mod.bulk_reason(headers, from_addr, subject, body):
        return "newsletter"

    # 1) Harde spam: verdachte domeinen of crypto/promo-patronen. Getoetst op
    #    het VOLLEDIGE afzenderadres (niet alleen het domein) — anders glipt
    #    een lokaal deel als "nieuwsbrief@merk.nl" door de domein-check heen.
    #    Deze winnen altijd van vraag-signalen (geen "?"-exceptie).
    if any(d in frm for d in SPAM_SENDER_DOMAINS if d) and not _is_trusted(dom):
        return "spam"
    if _count_hits(s, SPAM_SUBJECT_HINTS) >= 1 or _count_hits(b, SPAM_BODY_HINTS) >= 2:
        return "spam"

    # 2) Automatische system/meldingen (CI, reminders, jobalerts). Geen antwoord.
    if any(d in frm for d in SYSTEM_SENDER_DOMAINS if d) or _count_hits(s, SYSTEM_SUBJECT_HINTS) >= 1:
        return "other"  # blijft zichtbaar als 'other', niet als concept

    # 3) Newsletters / promo-updates.
    if _count_hits(s, NEWSLETTER_HINTS) >= 1 or _count_hits(b, NEWSLETTER_HINTS) >= 2:
        return "newsletter"

    # 4) Facturen / betalingen.
    if _count_hits(s, INVOICE_HINTS) >= 1 or _count_hits(b, INVOICE_HINTS) >= 2:
        return "invoice"

    # Afzender die naar een mailplatform ruikt (mkt./email./news.-subdomein,
    # of een lokaal deel als 'newsletter'/'mailing'). Op zichzelf te zwak om
    # iets weg te filteren — een mens mailt ook vanaf info@ — maar wél reden
    # om de lat hoger te leggen voordat we een ANTWOORD of AFSPRAAK in gang
    # zetten. Een mailing die toevallig een vraagteken bevat is geen vraag.
    marketing = bulk_mod.looks_like_marketing_sender(from_addr)

    # 4b) Afzegging/annulering wint van elk afspraak-signaal — zie CANCEL_HINTS.
    if _count_words(s + " " + b_clean, CANCEL_HINTS) >= 1:
        return "other"

    # 5) Afspraak-verzoek → agenda-agent. Sterk signaal volstaat alleen;
    #    zwak signaal + dag/tijd-context ook. De zwakke routes gelden niet
    #    voor marketing-afzenders: die sturen geen afspraak-verzoeken, en zo
    #    werd een nieuwsbrief over Apple een voorstel voor 30 mei 2027.
    #    Op b_clean (geciteerde historie + handtekening eraf), anders wint
    #    de CTA-regel in andermans footer het van de eigenlijke boodschap.
    if _count_words(s + " " + b_clean, APPOINTMENT_STRONG) >= 1:
        return "appointment"
    if not marketing:
        if _count_words(s + " " + b_clean, APPOINTMENT_HINTS) >= 2:
            return "appointment"
        if _count_words(s + " " + b_clean, APPOINTMENT_HINTS) >= 1 and _has_time_context(s + " " + b_clean):
            return "appointment"

    # 6) Échte vraag — pas ná alle bovenstaande filters, en vereis méér dan
    #    alleen een "?" (die zit ook in promo's). Minimaal 1 sterk vraag-woord
    #    OF ("?" + een werkwoord-achtig signaal).
    q_hits = _count_words(s + " " + b, QUESTION_HINTS)
    if marketing:
        # Geen "?"-kortsluiting en een hogere drempel: bij een mailplatform
        # moet de tekst echt op een vraag lijken vóórdat er een concept komt.
        return "question" if q_hits >= 3 else "newsletter"
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
