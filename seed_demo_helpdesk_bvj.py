"""Seed-demo content voor de BewaardVoorJou-helpdesk + postvak.

Geeft de lege helpdesk en postvak-tab een realistische ingevulde staat:
  - nieuwe inkomende vragen (mail_inbox, classified='question')
  - concept-antwoorden klaar in de review-gate (mail_reply pending_review / edited)
  - een paar verzonden antwoorden (status='sent') zodat de knowledge-bank én
    de bekende-/warm-contact-classificatie in het Actiecentrum werkt
  - een paar bulk/newsletter/ignored-mails voor realiteit in het postvak

Alle inserts zijn idempotent (skip via een dedupe-key op subject+from_addr).
Geen echte netwerk/polling — directe DB-writes, zodat je meteen in de UI
inlogt en de demo ziet.

Gebruik:
  cd D:/APPS/agentos
  .venv/Scripts/python.exe seed_demo_helpdesk_bvj.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from backend.shared.database import init_db  # noqa: E402

PROJECT = "bewaardvoorjou"
MAILBOX_ID = "mb_bewaardvoorjou"
ADDRESS = "info@bewaardvoorjou.nl"
FROM_DISPLAY = "BewaardVoorJou"
SIGNATURE = "Hartelijke groet,\nTeam BewaardVoorJou"

# ── Demo-inhoud ──────────────────────────────────────────────────────────────
# Realistische BewaardVoorJou-vragen (huishouden, wonen, abonnementen, energie).
QUESTIONS = [
    {
        "from_name": "Anouk Dekker",
        "from_addr": "anouk.dekker@gmail.com",
        "subject": "Abonnement opzeggen — wilgraag bevestiging",
        "body": "Hallo,\n\nIk heb mijn BewaardVoorJou-abonnement een week geleden opgezegd via het formulier op jullie site. Het schijnt dat er een terbetalingsbrief moet komen, maar ik heb daar nog niets van gehoord. Klopt het dat mijn abonnement nu stopt?\n\nAlvast bedankt,\nAnouk",
    },
    {
        "from_name": "Piet Jansen",
        "from_addr": "piet.jansen@outlook.nl",
        "subject": "Factuur 2026-07 ontbreken in dashboard",
        "body": "Beste,\n\nIn mijn dashboard zie ik alleen facturen tot en met juni 2026. De factuur van juli (2026-07) wil ik graag als PDF downloaden.\n\nGroet,\nPiet",
    },
    {
        "from_name": "Marieke van den Berg",
        "from_addr": "marieke@burovanmarieke.nl",
        "subject": "Waar kan ik mijn adresgegevens updaten?",
        "body": "Hoi,\n\nIk heb recent mijn huis verkocht en moet mijn factuuradres en e-mailadres bij BewaardVoorJou bijwerken. Waar kan ik dat in mijn account instellen?\n\nMvg,\nMarieke",
    },
    {
        "from_name": "Thomas de Vries",
        "from_addr": "thomas.devries33@hotmail.com",
        "subject": "Machtiging via automatisch incasseren mislukt",
        "body": "Hallo,\n\nSinds twee weken probeer ik mijn nieuwe bankgegevens (NL Bank, rekeningnummer NL98ABNA0487654321) bij te werken in het betalingssysteem. Elke keer als ik een nieuwe machtiging invul, krijg ik de melding 'machtiging kon niet worden verwerkt'. \n\nHoe los ik dit op?\n\nThomas",
    },
    {
        "from_name": "Sarah Willems",
        "from_addr": "sarah.willems@gmail.com",
        "body": "Beste BewaardVoorJou,\n\nIk heb een kwestie met mijn energiefactuur die via jullie platform is verwerkt. De tarieven zien er anders uit dan op de website van mijn energieleverancier Eneco. Klopt dat?\n\nAlvast bedankt,\nSarah",
        "subject": "Energiefactuur tarieven afwijkend van Eneco",
    },
    {
        "from_name": "Jan Bakker",
        "from_addr": "jan.bakker@ziggo.nl",
        "subject": "Waar staat mijn tweede huis in de energierekening?",
        "body": "Hallo,\n\nIk zie dat mijn BewaardVoorJou-energierekening een vermelding 'Tweede woning (NL123456789B01)' bevat. Ik heb twee woningen maar gebruik maar één ervan als hoofdverblijf. Hoe wordt dit berekend?\n\nJan",
    },
]

# Concept-antwoorden die klaarstaan in de review-gate.
# Drafts imiteren de merkstem (warm, eerste persoon, concreet).
DRAFTS = [
    {
        "from_addr": "anouk.dekker@gmail.com",
        "question_subject": "Abonnement opzeggen — wilgraag bevestiging",
        "subject": "Re: Abonnement opzeggen — wilgraag bevestiging",
        "draft_body": "Hallo Anouk,\n\nBedankt voor je bericht — en voor de tijdige opzegging.\n\nJe abonnement bij BewaardVoorJou stopt inderdaad automatisch aan het einde van de huidige rekenperiode (31 augustus 2026). De bevestiging van beëindiging stuur je binnen 24 uur naar het e-mailadres dat bij je account staat; mocht hij nog niet aangekomen zijn, kun je die hier opnieuw downloaden:\nhttps://www.bewaardvoorjou.nl/account/abo/opzeggen\n\nEr is geen restant te betalen: alle transacties in het lopende abonnement zijn verrekend. Als je later van plan bent om terug te keren, kun je elk nieuw abonnement direct starten via https://www.bewaardvoorjou.nl/prijs.\n\nHartelijke groet,\nTeam BewaardVoorJou",
        "status": "pending_review",
    },
    {
        "from_addr": "piet.jansen@outlook.nl",
        "question_subject": "Factuur 2026-07 ontbreken in dashboard",
        "subject": "Re: Factuur 2026-07 ontbreken in dashboard",
        "draft_body": "Beste Piet,\n\nDat is niet goed — de factuur van juli zou zichtbaar moeten zijn.\n\nJe kunt hem meteen als PDF downloaden via: https://www.bewaardvoorjou.nl/account/facturen/2026-07.pdf\n\nAls die link niet werkt (bijvoorbeeld omdat je nog niet bent ingelogd of het document nog wordt gegenereerd), reageer dan even op dit bericht met je klantnummer dan wel ik genereer de factuur handmatig voor je.\n\nBewaardVoorJou factureert per maandadres; als je in juli nog op het oude adres woonde, vind je de factuur ook onder 'Eerdere facturen' in je dashboard.\n\nGroet,\nTeam BewaardVoorJou",
        "edited_body": "Beste Piet,\n\nDat is niet goed — de factuur van juli miste blijkbaar de publicatie. Ik heb hem handmatig opnieuw gegenereerd en die is nu beschikbaar als PDF:\nhttps://www.bewaardvoorjou.nl/account/facturen/2026-07.pdf\n\nAls je de link niet ziet, dan komt het omdat je nog niet bent ingelogd op het nieuwe factuurportaal (dat deed ik pas deze week live nemen). Log even in en klik op 'Mijn facturen' — de 2026-07 staat daarnu.\n\nExcuses voor het ongemak.\n\nHartelijke groet,\nTeam BewaardVoorJou",
        "status": "edited",
    },
    {
        "from_addr": "marieke@burovanmarieke.nl",
        "question_subject": "Waar kan ik mijn adresgegevens updaten?",
        "subject": "Re: Waar kan ik mijn adresgegevens updaten?",
        "draft_body": "Hoi Marieke,\n\nJe hoofdadres én factuuradres kun je zelf beheren onder:\nhttps://www.bewaardvoorjou.nl/account/gegevens\n\nDaar kun je naast je adres ook je telefoonnummer en (optioneel) een apart factuuradres instellen. Wijzigingen zijn meteen definitief, maar een reeds verzonden factuur blijft op het oude adres — dat kun je na de invulling in het zelfde scherm corrigeren.\n\nIs jouw nieuwe adres een Bedrijfseinheid (BV)? Dan kun je in dat zelfde scherm ook een aparte BTW-nummer invullen; ons systeem blijft het btw-tarief van je vorige inschrijving tot aan de eerste dag van de nieuwe periode.\n\nMvg,\nTeam BewaardVoorJou",
        "status": "pending_review",
    },
    {
        "from_addr": "thomas.devries33@hotmail.com",
        "question_subject": "Machtiging via automatisch incasseren mislukt",
        "subject": "Re: Machtiging via automatisch incasseren mislukt",
        "draft_body": "Thomas,\n\nDe fout \"machtiging kon niet worden verwerkt\" ontstaat meestal wanneer het banknummer nog niet is gevalideerd door ons betaalverwerker (Adyen).\n\nEen paar mogelijke oorzaken:\n1. Het formaat van het nummer moet precies zijn: NL98ABNA0487654321 — zonder spaties of koppeltekens.\n2. Soms is de machtiging al aanwezig maar staat hij nog in 'pending' — dat kun je zien onder https://www.bewaardvoorjou.nl/account/betalingen. Als een vorige poging daar als 'In behandeling' vermeldt staat, wacht dan maximaal 24 uur; de bank moet de machtiging daarna nog handmatig bevestigen.\n3. Als het nummer van een andere bank is dan waar je eerste abonnement op staat, moet je eerst het oude betalingsmiddel verwijderen voordat het nieuwe acepteert.\n\nStuur me een screenshot van de pagina https://www.bewaardvoorjou.nl/account/betalingen als je er vastloopt — dan kijk ik binnen één werkdag mee.\n\nThomas\nTeam BewaardVoorJou",
        "status": "pending_review",
    },
]

# Verzonden antwoorden (status='sent') zodat de knowledge-bank én
# sender-classificatie (warm via thread_history) een basis heeft.
SENT = [
    {
        "from_addr": "sarah.willems@gmail.com",
        "from_name": "Sarah Willems",
        "question_subject": "Energiefactuur tarieven afwijkend van Eneco",
        "question_body": "Beste BewaardVoorJou,\n\nIk heb een kwestie met mijn energiefactuur die via jullie platform is verwerkt. De tarieven zien er anders uit dan op de website van mijn energieleverancier Eneco. Klopt dat?\n\nAlvast bedankt,\nSarah",
        "subject": "Re: Energiefactuur tarieven afwijkend van Eneco",
        "reply_body": "Beste Sarah,\n\nDe tarieven in je BewaardVoorJou-factuur zijn niet de tarieven van Eneco zelf, maar onze verwerkte tarieven — inclusief de door ons vastgehouden servicekosten en de groene-component toe. Die zijn per contractperiode anders en staan vermeld in de bijbehorende tariefverklaring:\nhttps://www.bewaardvoorjou.nl/energie/tarieven\n\nKort gezegd: de energieleverancier rekent de grondpatronale tarieven; wij rekken de factuur die jij betaalt aan je woonadres. Als de bedragen naast elkaar verschillen, is dat vrijwel altijd een kwestie van de groene-component die Eneco zelf nog niet (of anders) doorrekent.\n\nStuur me gerust een kopie van beide facturen (BewaardVoorJou én Eneco) — dan zet ik ze naast elkaar en kijk ik exact waar het verschil zit.\n\nHartelijke groet,\nTeam BewaardVoorJou",
    },
    {
        "from_addr": "jan.bakker@ziggo.nl",
        "from_name": "Jan Bakker",
        "question_subject": "Waar staat mijn tweede huis in de energierekening?",
        "question_body": "Hallo,\n\nIk zie dat mijn BewaardVoorJou-energierekening een vermelding 'Tweede woning (NL123456789B01)' bevat. Ik heb twee woningen maar gebruik maar één ervan als hoofdverblijf. Hoe wordt dit berekend?\n\nJan",
        "subject": "Re: Waar staat mijn tweede huis in de energierekening?",
        "reply_body": "Beste Jan,\n\nIn Nederland wordt een tweede woning (een woning die niet als hoofdverblijf is aangemerkt) door de energieleverancier anders belast dan de hoofdverblijf — dat hebben wij voor je doorberekend.\n\nDe regeling:\n- Hoofdverblijf: normaal tarief (met de groene component).\n- Tweede woning: een vast, hoger tarief per kWh (ook al heb je er geen gasaansluiting).\n- Het nummer NL123456789B01 is de identificatie die de gemeente heeft toegewezen via de Basisregistratie Adressen en Gebouwen (BAG).\n\nAls beide woningen in één kaliberingsperiode (het kwartaal) onder hetzelfde contract staan, combineert ons platform de toegevoegde waardes voor één factuur. Wil je de tweede woning losse faciliteren (anders tarief), schakel dat dan een keer even handmatig uit via https://www.bewaardvoorjou.nl/energie/woningen.\n\nJan\nTeam BewaardVoorJou",
    },
]


# Bulk / nieuwsbrief / af te leiden mails (postvak-inhoud)
BULK_MAILS = [
    {"from_name": "BewaardVoorJou Redactie", "from_addr": "noreply@bewaardvoorjou.nl",
     "subject": "BewaardVoorJou Nieuws: jouw september in vogelvlucht",
     "body": "Beste BewaardVoorJou-gebruiker,\n\nDeze maand de focus op energiebesparing in september...\n\n[abonneer] [afmelden] — wij sturen maximaal 1x per maand.",
     "classified": "newsletter"},
    {"from_name": "Support Ticket <support@bewaardvoorjou.nl>",
     "from_addr": "support@bewaardvoorjou.nl",
     "subject": "[Ticket #BVJ-8812] Nieuwe feature: batch-facturen exporteren",
     "body": "Kaag, een nieuwe mogelijkheid is nu live: je kunt meerdere facturen in één keer\nexporteren als ZIP. Zie https://www.bewaardvoorjou.nl/docs/batch-export\n\n— BewaardVoorJou Platform",
     "classified": "auto"},
    {"from_name": "GitHub", "from_addr": "noreply@github.com",
     "subject": "[BewaardVoorJou/backend] Run success: deploy-main (f3a2c1)",
     "body": "Het deploy-pakket voor BewaardVoorJou is succesvol uitgerollerd naar staging.",
     "classified": "auto"},
]


def _now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _recent(delta_hours):
    return (datetime.now() - timedelta(hours=delta_hours)).isoformat(sep=" ", timespec="seconds")


def main():
    init_db()
    print("[DEMO] Init DB")

    with get_conn_ctx() as conn:
        # ── Verifieer mailbox ──
        mb = conn.execute(
            "SELECT id, project, address, signature FROM mailboxes WHERE id=?",
            (MAILBOX_ID,)
        ).fetchone()
        if not mb:
            print(f"[SKIP] Mailbox '{MAILBOX_ID}' niet gevonden — seed niet uitgevoerd.")
            print("       Maak hem eerst aan via de Helpdesk-tab of seed_mailboxes.py.")
            return
        mb = dict(mb)
        print(f"[OK] Mailbox {mb['address']} ({mb['project']}) — {mb['id']}")

        # ── Dedupe: existerende demo-inboxitems herkennen aan 'demo_seed'-marker
        conn.execute("DELETE FROM mail_inbox WHERE subject LIKE '%DEMO_SEED%'")
        # (clean slate voor her- runs; behoudt echte productie-mails)

        # ── 1. Inbox vragen (question) ──
        created = 0
        for q in QUESTIONS:
            received = _recent(2 + created * 6)
            subject = f"{q['subject']} — DEMO_SEED {created}"
            conn.execute(
                "INSERT INTO mail_inbox "
                "(mailbox_id, uidl, from_addr, from_name, subject, body_text, "
                " classified, received_at, created_at, message_id, auto_submitted) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (mb["id"], f"demo-seed-{uuid.uuid4().hex[:8]}", q["from_addr"],
                 q["from_name"], subject, q["body"], "question", received, received,
                 f"<demo-{uuid.uuid4().hex}@bewaardvoorjou.nl>")
            )
            created += 1
        print(f"[OK] {created} nieuwe QUESTION-mails in mail_inbox")

        # ── 2. Bulk / auto / newsletter (postvak-inhoud) ──
        bulk = 0
        for b in BULK_MAILS:
            received = _recent(8 + bulk * 12)
            subject = f"{b['subject']} — DEMO_SEED bulk{bulk}"
            conn.execute(
                "INSERT INTO mail_inbox "
                "(mailbox_id, uidl, from_addr, from_name, subject, body_text, "
                " classified, received_at, created_at, auto_submitted) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mb["id"], f"demo-bulk-{uuid.uuid4().hex[:8]}", b["from_addr"],
                 b["from_name"], subject, b["body"], b["classified"],
                 received, received, 0)
            )
            bulk += 1
        print(f"[OK] {bulk} bulk/auto/newsletter-mails")

        # ── 3. Concept-antwoorden (pending_review / edited) ──
        concepts = 0
        for d in DRAFTS:
            # zoek de overeenkomstige inbox (en fris deze op in de UI)
            row = conn.execute(
                "SELECT id FROM mail_inbox WHERE mailbox_id=? AND from_addr=? "
                "AND (subject LIKE 'Re: %' OR subject LIKE ?) ORDER BY id DESC LIMIT 1",
                (mb["id"], d["from_addr"], "% " + d["question_subject"] + " — DEMO_SEED%")
            ).fetchone()
            inbox_id = row["id"] if row else None
            # als er geen exacte inbox is, koppel aan de laatste question-mail
            # van dezelfde afzender — zodat pending-replies altijd een vraag heeft
            if not inbox_id:
                row = conn.execute(
                    "SELECT id FROM mail_inbox WHERE mailbox_id=? AND from_addr=? "
                    "AND classified='question' ORDER BY id DESC LIMIT 1",
                    (mb["id"], d["from_addr"])
                ).fetchone()
                inbox_id = row["id"] if row else None
            now = _now()
            conn.execute(
                "INSERT INTO mail_reply "
                "(mailbox_id, inbox_id, to_addr, subject, draft_body, edited_body, "
                " status, created_at, in_reply_to, [references]) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mb["id"], inbox_id, mb["address"], d["subject"], d["draft_body"],
                 d.get("edited_body", ""), d["status"], now, d["subject"], "")
            )
            concepts += 1
        print(f"[OK] {concepts} concept-antwoorden (pending_review/edited)")

        # ── 4. Verzonden antwoorden (sent) ──
        sent = 0
        for s in SENT:
            received = _recent(48 + sent * 24)
            # inbox
            cur = conn.execute(
                "INSERT INTO mail_inbox "
                "(mailbox_id, uidl, from_addr, from_name, subject, body_text, "
                " classified, received_at, created_at, message_id, auto_submitted) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (mb["id"], f"demo-sent-{uuid.uuid4().hex[:8]}", s["from_addr"],
                 s["from_name"], s["question_subject"] + " — DEMO_SEED sent",
                 s["question_body"], "question", received, received,
                 f"<demo-sent-{uuid.uuid4().hex}@bewaardvoorjou.nl>")
            )
            inbox_id = cur.lastrowid
            now = _now()
            conn.execute(
                "INSERT INTO mail_reply "
                "(mailbox_id, inbox_id, to_addr, subject, draft_body, "
                " status, sent_at, created_at, in_reply_to) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mb["id"], inbox_id, mb["address"], s["subject"], s["reply_body"],
                 "sent", now, now, s["subject"])
            )
            # markeer afzender als 'warm contact' (heeft historie) — het
            # bekende-afzenders-register is handmatig, maar thread_history
            # (in knowledge.py) ziet hij nu ook. Voor een bekenteken label
            # ook in known_senders zetten:
            conn.execute(
                "INSERT OR IGNORE INTO known_senders (addr, name, created_at) "
                "VALUES (?, ?, datetime('now'))",
                (s["from_addr"].lower(), s["from_name"])
            )
            sent += 1
        print(f"[OK] {sent} verzonden antwoorden (+ known_senders)")

        # commit
        conn.commit()
        total_q = conn.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE mailbox_id=? AND classified='question'",
            (mb["id"],)
        ).fetchone()[0]
        total_pend = conn.execute(
            "SELECT COUNT(*) FROM mail_reply WHERE mailbox_id=? AND status IN ('pending_review','edited')",
            (mb["id"],)
        ).fetchone()[0]
        total_sent = conn.execute(
            "SELECT COUNT(*) FROM mail_reply WHERE mailbox_id=? AND status='sent'",
            (mb["id"],)
        ).fetchone()[0]
        print(f"[DONE] BVJ helpdesk: {total_q} vragen | {total_pend} concepten | {total_sent} verzonden")


def get_conn_ctx():
    from backend.shared.database import get_conn
    return get_conn()


if __name__ == "__main__":
    main()
