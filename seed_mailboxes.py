"""
Seed drie helpdesk-mailboxen in AgentOS vanuit de Outlook .reg exports.

De .reg bestanden (outlook_info/outlook_hello) bevatten ALLEEN server-instellingen
(POP3 110 / SMTP 587, host mail.<domein>), GEEN wachtwoorden. Dit script haalt de
wachtwoorden uit environment-variabelen zodat er niets geheims op schijf komt.

Per account:
  - POP3 user  = het e-mailadres (zoals in de .reg)
  - SMTP user  = het e-mailadres
  - password   = 1 env-var voor zowel POP3 als SMTP (zelfde host = zelfde wachtwoord)

Gebruik:
  export MAIL_PW_DATINGASSISTENT='...'
  export MAIL_PW_BIJEEN='...'
  export MAIL_PW_ICTUSGO='...'
  cd D:/apps/agentos
  python3 seed_mailboxes.py

Een box zonder wachtwoord wordt AANGEMAAKT MAAR disabled (enabled=0), zodat de
scheduler er niet op vastloopt. Zodra het wachtwoord er staat:
  UPDATE mailboxes SET enabled=1 WHERE id='mb_...';
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from backend.shared.database import init_db  # noqa: E402
from backend.domains.mail.service import create_mailbox, update_mailbox  # noqa: E402

# Afgeleid uit de .reg exports (REGEDIT4 Internet Account Manager):
#   info@datingassistent.nl  -> mail.datingassistent.nl
#   hello@bijeen.app         -> mail.bijeen.app
#   info@ictusgo.nl          -> mail.ictusgo.nl
# POP3 Port 0x6e=110, SMTP Port 0x24b=587, geen SSL (STARTTLS).
BOXES = [
    {
        "id": "mb_datingassistent",
        "project": "datingassistent",
        "label": "DatingAssistent helpdesk",
        "address": "info@datingassistent.nl",
        "pop_host": "mail.datingassistent.nl",
        "pop_port": 110,
        "pop_user": "info@datingassistent.nl",
        "smtp_host": "mail.datingassistent.nl",
        "smtp_port": 587,
        "smtp_user": "info@datingassistent.nl",
        "brand_context": "datingassistent",
        "knowledge_scope": "all",
        "poll_minutes": 30,
        "from_display": "DatingAssistent",
        "signature": "DatingAssistent",
        "pw_env": "MAIL_PW_DATINGASSISTENT",
    },
    {
        "id": "mb_bijeen",
        "project": "bijeen",
        "label": "Bijeen helpdesk",
        "address": "hello@bijeen.app",
        "pop_host": "mail.bijeen.app",
        "pop_port": 110,
        "pop_user": "hello@bijeen.app",
        "smtp_host": "mail.bijeen.app",
        "smtp_port": 587,
        "smtp_user": "hello@bijeen.app",
        "brand_context": "bijeen",
        "knowledge_scope": "all",
        "poll_minutes": 30,
        "from_display": "Bijeen",
        "signature": "Bijeen",
        "pw_env": "MAIL_PW_BIJEEN",
    },
    {
        "id": "mb_ictusgo",
        "project": "ictusgo",
        "label": "IctusGo helpdesk",
        "address": "info@ictusgo.nl",
        "pop_host": "mail.ictusgo.nl",
        "pop_port": 110,
        "pop_user": "info@ictusgo.nl",
        "smtp_host": "mail.ictusgo.nl",
        "smtp_port": 587,
        "smtp_user": "info@ictusgo.nl",
        "brand_context": "ictusgo",
        "knowledge_scope": "all",
        "poll_minutes": 30,
        "from_display": "IctusGo",
        "signature": "IctusGo",
        "pw_env": "MAIL_PW_ICTUSGO",
    },
]


def main():
    init_db()
    for b in BOXES:
        pw = os.getenv(b["pw_env"], "").strip()
        if not pw:
            print(f"[SKIP-PASSWORD] {b['address']}: geen ${b['pw_env']} — "
                  f"mailbox wordt aangemaakt MAAR disabled (enabled=0).")
            # Maak aan zonder wachtwoord, disabled, zodat de structuur klaarstaat.
            create_mailbox({**b, "pop_password": "", "smtp_password": "", "enabled": 0})
            print(f"        Zet later het wachtwoord en enable:")
            print(f"        UPDATE mailboxes SET pop_password='...', smtp_password='...', "
                  f"enabled=1 WHERE id='{b['id']}';")
            continue
        create_mailbox({
            **b,
            "pop_password": pw,
            "smtp_password": pw,
            "enabled": 1,
        })
        print(f"[OK] {b['address']} geregistreerd als helpdesk-mailbox (enabled=1).")

    print("\nKlaar. Controleer met:")
    print("  python3 -c \"from backend.shared.database import get_conn;"
          " [print(dict(r)) for r in get_conn().execute("
          "'SELECT id,project,address,enabled FROM mailboxes')]\"")


if __name__ == "__main__":
    main()
