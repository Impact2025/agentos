"""Unit-tests voor mail/ticket.py — het ontpakken en het vangnet.

Geen DB, geen netwerk: pure functies.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
IMPACTOS_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, IMPACTOS_ROOT)

from backend.domains.mail import ticket  # noqa: E402


def test_unwrap_bewaardvoorjou_sjabloon():
    body = (
        "Ticket: BVJ-0002\n"
        "Categorie: Overig\n"
        "Onderwerp: Vraag over levering\n"
        "Van: Jan de Vries\n"
        "E-mail: jan@klant.nl\n"
        "Bericht:\n"
        "Wanneer wordt mijn bestelling geleverd?"
    )
    out = ticket.unwrap_ticket_notification(
        "Nieuwe vraag: Overig", body, "noreply@bewaardvoorjou.nl", "bewaardvoorjou.nl"
    )
    assert out is not None
    assert out["customer_email"] == "jan@klant.nl"
    assert "geleverd" in out["question"]


def test_unwrap_geeft_none_bij_ontbrekend_sjabloon():
    # Precies het LiefdeVoorIedereen-geval (21 aug 2026): een support-tool
    # met een heel ander velden-sjabloon dan Bewaardvoorjou's Ticket/.../Bericht.
    out = ticket.unwrap_ticket_notification(
        "[Support] Nieuw Ticket #WSW613MH - MESSAGES",
        "Er is een nieuw ticket aangemaakt in het systeem.",
        "noreply@liefdevooriedereen.nl",
        "liefdevooriedereen.nl",
    )
    assert out is None


def test_looks_like_ticket_notification_vangt_onbekend_sjabloon():
    # Dit is precies waarom het vangnet bestaat: unwrap faalt (vorige test),
    # maar de onderwerpregel verraadt alsnog dat dit een ticket is.
    assert ticket.looks_like_ticket_notification(
        "[Support] Nieuw Ticket #WSW613MH - MESSAGES",
        "noreply@liefdevooriedereen.nl",
        "liefdevooriedereen.nl",
    )
    assert ticket.looks_like_ticket_notification(
        "Nieuwe vraag: Overig — vraag van lid — BVJ-0002",
        "noreply@bewaardvoorjou.nl",
        "bewaardvoorjou.nl",
    )


def test_looks_like_ticket_notification_alleen_eigen_domein():
    # Een concurrent of externe partij met een gelijkend onderwerp mag niet
    # meetellen — anders wordt dit vangnet zelf een lek voor willekeurige mail.
    assert not ticket.looks_like_ticket_notification(
        "Nieuw ticket aangemaakt bij Concurrent BV",
        "noreply@concurrent.nl",
        "liefdevooriedereen.nl",
    )


def test_looks_like_ticket_notification_geen_valse_positief_op_gewone_mail():
    assert not ticket.looks_like_ticket_notification(
        "Bevestiging van je bestelling",
        "orders@liefdevooriedereen.nl",
        "liefdevooriedereen.nl",
    )
