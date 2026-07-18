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


# ── Negeerlijst ("Niet meer reageren") ──────────────────────────────────────
# Persoonlijke maildomeinen krijgen nooit een domein-brede blokkade — anders
# blokkeert één vervelende gmail-afzender heel gmail.com.
_FREEMAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "live.nl", "hotmail.nl", "outlook.nl", "yahoo.com", "yahoo.nl",
    "icloud.com", "me.com", "ziggo.nl", "kpnmail.nl", "kpn.nl", "planet.nl",
    "home.nl", "xs4all.nl", "chello.nl", "hetnet.nl", "casema.nl", "upcmail.nl",
    "protonmail.com", "proton.me", "gmx.com", "gmx.net", "mail.com",
)


def _extract_email(from_addr: str) -> str:
    """'Naam <x@y.nl>' → 'x@y.nl' (lowercase). Zonder haken: het adres zelf."""
    import re
    m = re.search(r"<([^>]+)>", from_addr or "")
    addr = (m.group(1) if m else (from_addr or "")).strip().lower()
    return addr


def is_ignored_sender(conn, from_addr: str) -> bool:
    """Staat deze afzender (exact adres of '@domein') op de negeerlijst?"""
    addr = _extract_email(from_addr)
    if not addr or "@" not in addr:
        return False
    domain_pat = "@" + addr.split("@", 1)[1]
    row = conn.execute(
        "SELECT 1 FROM mail_ignored_senders WHERE pattern=? OR pattern=? LIMIT 1",
        (addr, domain_pat),
    ).fetchone()
    return row is not None


def ignore_sender(reply_id: int) -> Optional[Dict]:
    """'Niet meer reageren': zet de afzender van dit concept op de negeerlijst,
    wijs alle openstaande concepten van dezelfde afzender af, en markeer de
    onderliggende inbox-mails als 'ignored'. Zakelijke domeinen (niet-freemail)
    worden domein-breed geblokkeerd, zodat bv. élk iBood-adres stopt.
    Retourneert {'address', 'domain_blocked'} of None als het concept weg is."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT r.id, r.to_addr, r.subject, i.from_addr "
            "FROM mail_reply r LEFT JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.id=?", (reply_id,),
        ).fetchone()
        if not r:
            return None
        addr = _extract_email(r["from_addr"] or r["to_addr"])
        if not addr or "@" not in addr:
            return None
        domain = addr.split("@", 1)[1]
        reason = (r["subject"] or "")[:200]
        conn.execute(
            "INSERT OR IGNORE INTO mail_ignored_senders(pattern, reason) VALUES(?,?)",
            (addr, reason),
        )
        domain_blocked = domain not in _FREEMAIL_DOMAINS
        if domain_blocked:
            conn.execute(
                "INSERT OR IGNORE INTO mail_ignored_senders(pattern, reason) VALUES(?,?)",
                ("@" + domain, reason),
            )
        # Alle openstaande concepten van deze afzender (of dit domein) afwijzen.
        like = "%@" + domain + "%" if domain_blocked else "%" + addr + "%"
        conn.execute(
            "UPDATE mail_reply SET status='rejected' "
            "WHERE status IN ('pending_review','edited') AND ("
            "  lower(to_addr) LIKE ? OR inbox_id IN ("
            "    SELECT id FROM mail_inbox WHERE lower(from_addr) LIKE ?))",
            (like, like),
        )
        conn.execute(
            "UPDATE mail_inbox SET classified='ignored' WHERE lower(from_addr) LIKE ?",
            (like,),
        )
        return {"address": addr, "domain_blocked": domain_blocked}


def list_ignored_senders() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, pattern, reason, created_at FROM mail_ignored_senders "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def unignore_sender(ignored_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM mail_ignored_senders WHERE id=?", (ignored_id,))
        return cur.rowcount > 0


def _run_mailbox_graph(mailbox: Dict) -> int:
    """Office365/Exchange-mailbox via Microsoft Graph (OAuth2 client_credentials).

    Leest de inbox via Graph, dedupet op bericht-id (vervangt POP3-UIDL),
    classificeert en klaart alleen 'question'-mails als concept in het
    Actiecentrum. Identiek gedrag aan de POP3-flow, alleen de transport-laag
    is anders.
    """
    from . import graph as graph_mod
    mid = mailbox["id"]
    try:
        fetched = graph_mod.fetch_messages(mailbox, top=25)
    except Exception:
        logger.exception("Graph-mailbox %s (%s) ophalen mislukt", mid, mailbox.get("address"))
        raise RuntimeError(
            f"Graph-mail ophalen mislukt voor {mailbox.get('address')}: "
            f"controleer de Entra-app-machtigingen (Mail.ReadWrite + beheerdersinstemming)."
        )

    with get_conn() as conn:
        seen = {r["uidl"] for r in conn.execute(
            "SELECT uidl FROM mail_inbox WHERE mailbox_id=?", (mid,))}
        created = 0
        for m in fetched:
            uidl = m["uidl"]
            if uidl in seen:
                continue
            auto_sub = graph_mod._is_auto_submitted(m)
            label = "auto" if auto_sub else "unknown"
            from_addr = m["from_addr"]
            if graph_mod._should_ignore(from_addr, m["subject"], m["body_text"], auto_sub):
                label = "newsletter" if graph_mod._looks_like_newsletter(m["subject"], m["body_text"]) else (
                    "auto" if auto_sub else "spam")
                conn.execute(
                    "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,"
                    "classified,message_id,in_reply_to,\"references\",auto_submitted) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (mid, uidl, from_addr, m["subject"], "", label,
                     m.get("message_id"), m.get("in_reply_to"), m.get("references"),
                     1 if auto_sub else 0),
                )
                continue
            from_name = m["from_name"]
            cur = conn.execute(
                "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,from_name,subject,body_text,"
                "classified,message_id,in_reply_to,\"references\",auto_submitted) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (mid, uidl, from_addr, from_name, m["subject"], m["body_text"],
                 "unknown", m.get("message_id"), m.get("in_reply_to"),
                 m.get("references"), 1 if auto_sub else 0),
            )
            if m["body_text"].strip():
                if is_ignored_sender(conn, from_addr):
                    conn.execute("UPDATE mail_inbox SET classified='ignored' WHERE id=?",
                                 (cur.lastrowid,))
                    continue
                kind = classify.classify(m["subject"], m["body_text"], from_addr)
                conn.execute("UPDATE mail_inbox SET classified=? WHERE id=?", (kind, cur.lastrowid))
                if kind == "appointment":
                    # Agenda-voorstel i.p.v. mailconcept (zie _process_classified).
                    if _process_classified(conn, mailbox, cur.lastrowid, from_addr,
                                           m["subject"], m["body_text"], kind):
                        created += 1
                    continue
                if kind != "question":
                    continue
                created += _process_classified(conn, mailbox, cur.lastrowid, from_addr,
                                               m["subject"], m["body_text"], kind)
    return created


def _process_classified(conn, mailbox: Dict, inbox_id: int, from_addr: str,
                        subject: str, body: str, kind: str) -> int:
    """Gedeelde verwerking voor 'question' (mailconcept) en 'appointment'
    (agenda-voorstel). Retourneert 1 als er iets klaargezet is, anders 0.

    Centraal zodat de POP3- en Graph-flow identiek gedrag tonen.
    """
    mid = mailbox["id"]
    from_name = (from_addr.split("<")[0].strip().strip('"') if "<" in from_addr
                 else from_addr)
    if kind == "appointment":
        from ..calendar import agent as agenda_agent
        prop = agenda_agent.create_proposal(mid, inbox_id, subject, from_addr, body)
        return 1 if prop else 0
    # question → concept-antwoord
    knowledge = knowledge_mod.build_knowledge(conn, mailbox.get("project", ""), mailbox)
    history = knowledge_mod.thread_history(conn, mid, from_addr, inbox_id)
    signature = (mailbox.get("signature") or "").strip()
    draft = drafter.draft_reply(
        from_name=from_name or from_addr,
        subject=subject,
        body=body,
        brand_context=mailbox.get("project", ""),
        knowledge=knowledge,
        history=history,
        has_signature=bool(signature),
    )
    if signature:
        draft = draft.rstrip() + "\n\n" + signature
    refs = ""
    irt = ""
    conn.execute(
        "INSERT INTO mail_reply(mailbox_id,inbox_id,to_addr,subject,draft_body,"
        "in_reply_to,\"references\") "
        "VALUES(?,?,?,?,?,?,?)",
        (mid, inbox_id, from_addr, "Re: " + subject, draft, irt, refs),
    )
    return 1


def run_mailbox(mailbox: Dict) -> int:
    """Verwerk één mailbox. Geeft aantal nieuwe concept-antwoorden terug."""
    mid = mailbox["id"]
    # Office365/Exchange-mailboxen: Graph-API in plaats van basic-auth POP3.
    if mailbox.get("auth_method") == "graph":
        return _run_mailbox_graph(mailbox)
    try:
        with get_conn() as conn:
            fetched = inbox.fetch_new(
                mailbox_id=mid,
                host=mailbox["pop_host"],
                port=int(mailbox["pop_port"] or 110),
                user=mailbox["pop_user"],
                pw=mailbox["pop_password"],
                conn=conn,
                use_ssl=bool(int(mailbox.get("pop_ssl") or 0)),
            )
            if not fetched:
                return 0
            knowledge = knowledge_mod.build_knowledge(
                conn, mailbox.get("project", ""), mailbox
            )
            created = 0
            for m in fetched:
                if is_ignored_sender(conn, m["from_addr"]):
                    conn.execute(
                        "UPDATE mail_inbox SET classified='ignored' WHERE id=?",
                        (m["id"],),
                    )
                    continue
                kind = classify.classify(m["subject"], m["body_text"], m["from_addr"])
                conn.execute(
                    "UPDATE mail_inbox SET classified=? WHERE id=?",
                    (kind, m["id"]),
                )
                if kind == "appointment":
                    created += _process_classified(
                        conn, mailbox, m["id"], m["from_addr"], m["subject"], m["body_text"], kind)
                    continue
                if kind != "question":
                    continue
                created += _process_classified(
                    conn, mailbox, m["id"], m["from_addr"], m["subject"], m["body_text"], kind)
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
    ok, sent_info = _send_reply_impl(reply_id)
    # Loggen pas ná de commit van de verzend-transactie — log_outcome opent een
    # eigen schrijf-connectie en zou anders op de open transactie vastlopen.
    if ok and sent_info:
        _log_sent(*sent_info)
    return ok


def _send_reply_impl(reply_id: int):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr, make_msgid

    with get_conn() as conn:
        r = conn.execute(
            "SELECT r.to_addr, r.subject, r.draft_body, r.edited_body, r.status, "
            "r.in_reply_to, r.\"references\", "
            "r.mailbox_id, "
            "m.smtp_host, m.smtp_port, m.smtp_user, m.smtp_password, m.address, "
            "m.from_display, m.project, m.signature, m.auth_method, "
            "m.graph_tenant_id, m.graph_client_id, m.graph_client_secret, m.graph_user_upn "
            "FROM mail_reply r JOIN mailboxes m ON m.id=r.mailbox_id WHERE r.id=?",
            (reply_id,),
        ).fetchone()
        if not r:
            return False, None
        if r["status"] == "sent":
            return True, None
        row = dict(r)  # sqlite3.Row kent geen .get()
        # Office365/Exchange: verstuur via Graph (geen SMTP basic-auth meer).
        if row.get("auth_method") == "graph":
            from . import graph as graph_mod
            if not graph_mod.is_configured(row):
                logger.error("Mailbox %s staat op graph maar mist Graph-credentials",
                             row.get("mailbox_id") or "?")
                return False, None
            body = row["edited_body"] or row["draft_body"]
            text = body + (f"\n\n—\n{row['project']} helpdesk · dit bericht is voorbereid met Agent OS. "
                           f"Tip: stuur gewoon een reply, we lezen mee." if not (row.get("signature") or "").strip() else "")
            try:
                graph_mod.send_message(
                    row, row["to_addr"], row["subject"], text,
                    from_display=row.get("from_display") or "",
                    in_reply_to=row.get("in_reply_to") or "",
                    references=row.get("references") or "",
                )
                conn.execute("UPDATE mail_reply SET status='sent', sent_at=datetime('now') WHERE id=?", (reply_id,))
                return True, (row["project"], row["to_addr"], row["subject"])
            except Exception as e:
                logger.exception("Graph-versturen mislukt voor reply %s: %s", reply_id, e)
                return False, None
        if not (r["smtp_host"] and r["smtp_user"] and r["smtp_password"]):
            # Terugval op de globale .env-SMTP (bestaande email_service)
            from ...shared.email_service import send_report
            body = r["edited_body"] or r["draft_body"]
            ok = send_report(subject=r["subject"], body=body, to=r["to_addr"])
            if ok:
                conn.execute("UPDATE mail_reply SET status='sent', sent_at=datetime('now') WHERE id=?", (reply_id,))
                return True, (r["project"], r["to_addr"], r["subject"])
            return False, None

        body = r["edited_body"] or r["draft_body"]
        # Handtekening zit al ín het concept (WYSIWYG). Alleen mailboxen zónder
        # eigen handtekening krijgen de generieke footer als vangnet.
        if (r["signature"] or "").strip():
            text = body
        else:
            text = body + (
                f"\n\n—\n{r['project']} helpdesk · dit bericht is voorbereid met Agent OS. "
                f"Tip: stuur gewoon een reply, we lezen mee."
            )
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
            return True, (r["project"], r["to_addr"], r["subject"])
        except Exception as e:
            logger.exception("Versturen via mailbox SMTP mislukt: %s", e)
            return False, None


def _log_sent(project: str, to_addr: str, subject: str) -> None:
    """Uitkomst-kaart per verzonden antwoord — zo is in de Activiteit per project
    terug te zien wat de helpdesk (na goedkeuring) heeft beantwoord."""
    try:
        from ...shared.outcomes import log_outcome
        log_outcome(
            project=project or "Helpdesk",
            action="mail_verstuurd",
            detail=f"Helpdesk-antwoord verstuurd aan {to_addr}: {subject}",
            artifact=f"mailto:{to_addr}",
            status="ok",
        )
    except Exception:
        logger.exception("Kon verzonden mail niet in activity_log zetten")


def reject_reply(reply_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE mail_reply SET status='rejected' WHERE id=?", (reply_id,))


def reject_replies_bulk(reply_ids: List[int]) -> int:
    """Wijs meerdere concepten in één keer af. Retourneert het aantal bijgewerkte rijen."""
    ids = [int(i) for i in (reply_ids or [])]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE mail_reply SET status='rejected' WHERE id IN ({placeholders})",
            ids,
        )
        return cur.rowcount or 0


def delete_replies_bulk(reply_ids: List[int]) -> int:
    """Definitief verwijderen: wist de concepten ÉN markeert de onderliggende
    inbox-mails als 'deleted' (tombstone). De tombstone blijft staan zodat de
    POP3-poller ze via UIDL-dedup niet opnieuw ophaalt en géén nieuw concept
    maakt. De fysieke mail op de mailserver zelf blijft ongemoeid.
    Retourneert het aantal verwijderde concepten."""
    ids = [int(i) for i in (reply_ids or [])]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        inbox_ids = [
            r["inbox_id"]
            for r in conn.execute(
                f"SELECT inbox_id FROM mail_reply WHERE id IN ({placeholders})", ids
            ).fetchall()
            if r["inbox_id"] is not None
        ]
        cur = conn.execute(
            f"DELETE FROM mail_reply WHERE id IN ({placeholders})", ids
        )
        if inbox_ids:
            iph = ",".join("?" for _ in inbox_ids)
            conn.execute(
                f"UPDATE mail_inbox SET classified='deleted' WHERE id IN ({iph})",
                inbox_ids,
            )
        return cur.rowcount or 0


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
            "SELECT id, project, label, address, pop_host, pop_port, pop_ssl, smtp_host, "
            "smtp_port, brand_context, knowledge_scope, poll_minutes, enabled, "
            "from_display, signature, auth_method, graph_tenant_id, graph_client_id, "
            "graph_user_upn FROM mailboxes ORDER BY project, address"
        ).fetchall()
    boxes = [dict(r) for r in rows]
    if project:
        boxes = [b for b in boxes if _norm(b["project"]) == _norm(project)]
    return boxes


def create_mailbox(data: Dict) -> str:
    import uuid
    from datetime import datetime
    mid = data.get("id") or f"mb_{uuid.uuid4().hex[:12]}"
    cols = (
        "id,project,label,address,pop_host,pop_port,pop_ssl,pop_user,"
        "pop_password,smtp_host,smtp_port,smtp_user,smtp_password,brand_context,"
        "knowledge_scope,poll_minutes,enabled,from_display,signature,created_at,"
        "auth_method,graph_tenant_id,graph_client_id,graph_client_secret,graph_user_upn"
    )
    vals = (
        mid, data["project"], data.get("label", ""), data["address"],
        data["pop_host"], int(data.get("pop_port", 110)),
        int(data.get("pop_ssl", 0)), data["pop_user"],
        data.get("pop_password", ""), data.get("smtp_host", ""),
        int(data.get("smtp_port", 587)), data.get("smtp_user", ""),
        data.get("smtp_password", ""), data.get("brand_context", ""),
        data.get("knowledge_scope", "all"), int(data.get("poll_minutes", 30)),
        int(data.get("enabled", 1)), data.get("from_display", ""),
        data.get("signature", ""), datetime.now().isoformat(),
        data.get("auth_method", "pop"), data.get("graph_tenant_id", ""),
        data.get("graph_client_id", ""), data.get("graph_client_secret", ""),
        data.get("graph_user_upn", ""),
    )
    placeholders = ",".join(["?"] * len(vals))
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO mailboxes({cols}) VALUES({placeholders})",
            vals,
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
        "signature", "auth_method", "graph_tenant_id", "graph_client_id",
        "graph_client_secret", "graph_user_upn",
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
