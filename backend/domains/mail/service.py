"""Mail helpdesk — pipeline + per-mailbox runner.

Flow per mailbox:
  fetch_new (POP3, dedupe UIDL)
    -> classify (question|invoice|newsletter|other|spam|auto)
    -> alleen 'question': drafter schrijft NL-concept
    -> mail_reply (status=pending_review) klaar in Actiecentrum
Alles anders blijft in mail_inbox met een label, niets vertrekt.
"""
import logging
import smtplib
from typing import List, Dict, Optional

from ...shared.database import get_conn
from . import inbox, classify, drafter, knowledge as knowledge_mod

logger = logging.getLogger(__name__)


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def run_mailbox(mailbox: Dict) -> int:
    """Verwerk één mailbox. Geeft aantal nieuwe concept-antwoorden terug."""
    mid = mailbox["id"]
    try:
        with get_conn() as conn:
            fetched = inbox.fetch_new(
                mailbox_id=mid,
                host=mailbox["pop_host"],
                port=int(mailbox["pop_port"] or 110),
                user=mailbox["pop_user"],
                pw=mailbox["pop_password"],
                conn=conn,
            )
            if not fetched:
                return 0
            knowledge = knowledge_mod.build_knowledge(
                conn, mailbox.get("project", ""), mailbox
            )
            created = 0
            for m in fetched:
                kind = classify.classify(m["subject"], m["body_text"])
                conn.execute(
                    "UPDATE mail_inbox SET classified=? WHERE id=?",
                    (kind, m["id"]),
                )
                if kind != "question":
                    continue
                history = knowledge_mod.thread_history(
                    conn, mid, m["from_addr"], m["id"]
                )
                draft = drafter.draft_reply(
                    from_name=m["from_name"] or m["from_addr"],
                    subject=m["subject"],
                    body=m["body_text"],
                    brand_context=mailbox.get("project", ""),
                    knowledge=knowledge,
                    history=history,
                )
                # Thread het antwoord op de originele mail
                refs = (m.get("references") or "").strip()
                irt = (m.get("in_reply_to") or "").strip()
                chain = f"{refs} {irt}".strip()
                conn.execute(
                    "INSERT INTO mail_reply(mailbox_id,inbox_id,to_addr,subject,draft_body,"
                    "in_reply_to,\"references\") "
                    "VALUES(?,?,?,?,?,?,?)",
                    (mid, m["id"], m["from_addr"], "Re: " + m["subject"], draft,
                     m.get("message_id", ""), chain),
                )
                created += 1
            return created
    except Exception:
        logger.exception("Mailbox %s (%s) verwerking mislukt", mid, mailbox.get("address"))
        raise


def run_all_mailboxes(mailbox_id: Optional[str] = None) -> Dict[str, int]:
    """Verwerk elke ingeschakelde mailbox (of alleen `mailbox_id`).
    Returns {address: aantal_concepten}. Een kapotte mailbox (POP3/credentials)
    verschijnt als uitkomst-kaart met status='error' in het Actiecentrum —
    stil falen betekent hier: klanten die dagenlang geen antwoord krijgen."""
    results: Dict[str, int] = {}
    with get_conn() as conn:
        if mailbox_id:
            boxes = conn.execute(
                "SELECT * FROM mailboxes WHERE enabled=1 AND id=?", (mailbox_id,)
            ).fetchall()
        else:
            boxes = conn.execute("SELECT * FROM mailboxes WHERE enabled=1").fetchall()
    for mb in boxes:
        mb = dict(mb)
        try:
            n = run_mailbox(mb)
            results[mb["address"]] = n
        except Exception as e:
            results[mb["address"]] = -1  # fout
            try:
                from ...shared.outcomes import log_outcome
                log_outcome(
                    project=mb.get("project", "Helpdesk"),
                    action="mail_helpdesk",
                    detail=f"Mailbox {mb['address']} kon niet worden opgehaald: {e}",
                    next_step="Controleer de POP3-instellingen/het wachtwoord van deze mailbox op de Helpdesk-tab.",
                    status="error",
                )
            except Exception:
                logger.exception("Kon helpdesk-fout niet naar het Actiecentrum loggen")
    return results


# ── Actiecentrum-acties (menselijke goedkeuring vereist) ───────────────────

def send_reply(reply_id: int) -> bool:
    """Verstuur een goedgekeurd concept via de SMTP van déze mailbox (per project,
    niet de globale .env-SMTP). Threadt netjes op de originele mail en zet een
    heldere From-naam + helpdesk-footer. Review-gate: alleen via expliciete call."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr, make_msgid

    with get_conn() as conn:
        r = conn.execute(
            "SELECT r.to_addr, r.subject, r.draft_body, r.edited_body, r.status, "
            "r.in_reply_to, r.\"references\", "
            "m.smtp_host, m.smtp_port, m.smtp_user, m.smtp_password, m.address, "
            "m.from_display, m.project "
            "FROM mail_reply r JOIN mailboxes m ON m.id=r.mailbox_id WHERE r.id=?",
            (reply_id,),
        ).fetchone()
        if not r:
            return False
        if r["status"] == "sent":
            return True
        if not (r["smtp_host"] and r["smtp_user"] and r["smtp_password"]):
            # Terugval op de globale .env-SMTP (bestaande email_service)
            from ...shared.email_service import send_report
            body = r["edited_body"] or r["draft_body"]
            ok = send_report(subject=r["subject"], body=body, to=r["to_addr"])
            if ok:
                conn.execute("UPDATE mail_reply SET status='sent', sent_at=datetime('now') WHERE id=?", (reply_id,))
            return ok

        body = r["edited_body"] or r["draft_body"]
        # Plain-text body + nette footer (geen finance-stamp)
        footer = (
            f"\n\n—\n{r['project']} helpdesk · dit bericht is voorbereid met Agent OS. "
            f"Tip: stuur gewoon een reply, we lezen mee."
        )
        text = body + footer
        msg = MIMEMultipart("alternative")
        display = r["from_display"] or r["project"]
        msg["From"] = formataddr((display, r["address"]))
        msg["To"] = r["to_addr"]
        msg["Subject"] = r["subject"]
        msg["Message-ID"] = make_msgid(domain=r["address"].split("@")[-1])
        if r["in_reply_to"]:
            msg["In-Reply-To"] = r["in_reply_to"]
        if r["references"]:
            msg["References"] = r["references"]
        msg["Auto-Submitted"] = "no"
        msg.attach(MIMEText(text, "plain", "utf-8"))

        try:
            port = int(r["smtp_port"] or 587)
            with smtplib.SMTP(r["smtp_host"], port, timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.login(r["smtp_user"], r["smtp_password"])
                s.sendmail(r["address"], [r["to_addr"]], msg.as_string())
            conn.execute("UPDATE mail_reply SET status='sent', sent_at=datetime('now') WHERE id=?", (reply_id,))
            return True
        except Exception as e:
            logger.exception("Versturen via mailbox SMTP mislukt: %s", e)
            return False


def reject_reply(reply_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE mail_reply SET status='rejected' WHERE id=?", (reply_id,))


def edit_reply(reply_id: int, text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE mail_reply SET draft_body=?, edited_body=?, status='edited' WHERE id=?",
            (text, text, reply_id),
        )


# ── Mailbox-beheer (API) ────────────────────────────────────────────────────

def list_mailboxes(project: Optional[str] = None) -> List[Dict]:
    """Alle mailboxen, of alleen die van één project (naam-match zoals overal:
    genormaliseerd, zodat 'Skillkaart' en 'skillkaart' dezelfde zijn)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project, label, address, pop_host, pop_port, smtp_host, "
            "smtp_port, brand_context, knowledge_scope, poll_minutes, enabled, "
            "from_display FROM mailboxes ORDER BY project, address"
        ).fetchall()
    boxes = [dict(r) for r in rows]
    if project:
        boxes = [b for b in boxes if _norm(b["project"]) == _norm(project)]
    return boxes


def create_mailbox(data: Dict) -> str:
    import uuid
    mid = data.get("id") or f"mb_{uuid.uuid4().hex[:12]}"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO mailboxes(id,project,label,address,pop_host,pop_port,pop_user,"
            "pop_password,smtp_host,smtp_port,smtp_user,smtp_password,brand_context,"
            "knowledge_scope,poll_minutes,enabled,from_display,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (
                mid, data["project"], data.get("label", ""), data["address"],
                data["pop_host"], int(data.get("pop_port", 110)), data["pop_user"],
                data.get("pop_password", ""), data.get("smtp_host", ""), int(data.get("smtp_port", 587)),
                data.get("smtp_user", ""), data.get("smtp_password", ""),
                data.get("brand_context", ""), data.get("knowledge_scope", "all"),
                int(data.get("poll_minutes", 30)), int(data.get("enabled", 1)),
                data.get("from_display", ""),
            ),
        )
    return mid


def pending_replies(project: Optional[str] = None) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT r.id, r.mailbox_id, r.to_addr, r.subject, r.draft_body, "
            "r.edited_body, r.status, r.created_at, m.project, m.address, "
            "i.from_name, i.from_addr, i.subject AS question_subject, "
            "i.body_text AS question_body "
            "FROM mail_reply r "
            "JOIN mailboxes m ON m.id=r.mailbox_id "
            "LEFT JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.status IN ('pending_review','edited') ORDER BY r.created_at DESC"
        ).fetchall()
    replies = [dict(r) for r in rows]
    if project:
        replies = [r for r in replies if _norm(r["project"]) == _norm(project)]
    return replies


def update_mailbox(mailbox_id: str, data: Dict) -> bool:
    """Werk instelbare velden bij; lege wachtwoord-velden laten het oude staan."""
    allowed = (
        "project", "label", "address", "pop_host", "pop_port", "pop_user",
        "pop_password", "smtp_host", "smtp_port", "smtp_user", "smtp_password",
        "brand_context", "knowledge_scope", "poll_minutes", "enabled", "from_display",
    )
    updates, params = [], []
    for f in allowed:
        if f not in data or data[f] is None:
            continue
        if f in ("pop_password", "smtp_password") and not str(data[f]).strip():
            continue  # leeg wachtwoord = niet wijzigen
        updates.append(f"{f}=?")
        params.append(data[f])
    if not updates:
        return False
    params.append(mailbox_id)
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE mailboxes SET {', '.join(updates)} WHERE id=?", params)
        return cur.rowcount > 0


def delete_mailbox(mailbox_id: str) -> bool:
    """Verwijder een mailbox incl. inbox/replies (ON DELETE CASCADE)."""
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        cur = conn.execute("DELETE FROM mailboxes WHERE id=?", (mailbox_id,))
        return cur.rowcount > 0
