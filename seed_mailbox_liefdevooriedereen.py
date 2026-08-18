"""Seed de liefdevooriedereen.nl helpdesk-mailbox in AgentOS.

Afgeleid uit de Outlook .reg export:
  info@liefdevooriedereen.nl -> mail.liefdevooriedereen.nl
  POP3 Port 0x6e=110, SMTP Port 0x24b=587, geen SSL in de export.

Wachtwoord komt uit een env-var (niets geheims op schijf).
De box wordt aangemaakt met enabled=1 (pro, net als WeAreImpact) zodra het
wachtwoord er staat; de netwerk-check bepaalt of poort 110 (kaal) of
995 (POP3_SSL) de juiste is.

Gebruik:
  export LIE_PW='...'
  cd D:/apps/agentos
  python3 seed_mailbox_liefdevooriedereen.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from backend.shared.database import init_db  # noqa: E402
from backend.domains.mail.service import create_mailbox  # noqa: E402

BOX = {
    "id": "mb_liefdevooriedereen",
    "project": "LiefdeVoorIedereen",
    "label": "LiefdeVoorIedereen helpdesk",
    "address": "info@liefdevooriedereen.nl",
    # .reg-export waarden (niet-SSL). Pop-SSL poort 995 wordt gekozen indien de
    # live-check uitwijst dat 110 basic-auth geweigerd wordt.
    "pop_host": "mail.liefdevooriedereen.nl",
    "pop_port": 110,
    "pop_user": "info@liefdevooriedereen.nl",
    "smtp_host": "mail.liefdevooriedereen.nl",
    "smtp_port": 587,
    "smtp_user": "info@liefdevooriedereen.nl",
    "brand_context": "LiefdeVoorIedereen",
    "knowledge_scope": "all",
    "poll_minutes": 30,
    "from_display": "LiefdeVoorIedereen",
    "signature": "LiefdeVoorIedereen",
    "auth_method": "pop",
    "pw_env": "LIE_PW",
}


def main():
    init_db()
    pw = os.getenv(BOX["pw_env"], "").strip()
    if not pw:
        print(f"[SKIP] geen ${BOX['pw_env']} — mailbox NIET aangemaakt.")
        return
    mid = create_mailbox({
        **BOX,
        "pop_password": pw,
        "smtp_password": pw,
        "enabled": 1,
    })
    print(f"[OK] {BOX['address']} geregistreerd als helpdesk-mailbox "
          f"id={mid} (enabled=1).")


if __name__ == "__main__":
    main()
