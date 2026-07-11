"""POP3-poller voor één mailbox — dedupe via UIDL, spam/automatisch filter vooraf."""
import poplib
import email
import email.message
import re
from email.header import decode_header
from typing import List, Dict, Optional

# Afzenders die nooit een antwoord verdienen: nieuwsbrieven / hoster-spamrapporten.
SPAM_SENDERS = (
    "mail.vapidkeys.com",
    "spamrelay-pmgmaster.zxcs.nl",
)

# Onderwerpen / patronen die direct genegeerd worden (ZXCS daily spam report, enz.)
IGNORE_SUBJECT_HINTS = (
    "daily spam report",
    "web push",
    "getting started",
    "five ways to",
)

_ADDR_RE = re.compile(r"<([^>]+)>")


def _addr_only(raw: str) -> str:
    """'Klant <jan@x.nl>' -> 'jan@x.nl'. Voor de SMTP-verzending heeft de
    reply het zuivere adres nodig, anders strandt de mail."""
    if not raw:
        return ""
    m = _ADDR_RE.search(raw)
    return m.group(1).strip() if m else raw.strip()


def _looks_like_newsletter(subject: str, body: str) -> bool:
    s = (subject or "").lower()
    b = (body or "").lower()
    hints = ("unsubscribe", "view in browser", "web push", "getting started",
             "five ways to", "newsletter", "built for developers")
    return any(h in s or h in b for h in hints)


def _dm(s: Optional[str]) -> str:
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _strip_html(html: str) -> str:
    """Kale-tekst uit een HTML-mail — genoeg voor classificatie + beantwoording.
    Sommige mailclients sturen alléén text/html; zonder dit blijft de body leeg
    en wordt de vraag nooit als vraag herkend."""
    import html as html_mod
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _body(msg: email.message.Message) -> str:
    html_fallback = ""
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition"))
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True) or b""
            decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if part.get_content_type() == "text/plain":
                return decoded
            if part.get_content_type() == "text/html" and not html_fallback:
                html_fallback = decoded
        return _strip_html(html_fallback) if html_fallback else ""
    payload = msg.get_payload(decode=True) or b""
    decoded = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    if msg.get_content_type() == "text/html":
        return _strip_html(decoded)
    return decoded


def _should_ignore(from_addr: str, subject: str, body: str = "", auto_submitted: bool = False) -> bool:
    # Nooit antwoorden op auto-generated mail (out-of-office, bounce, vacation,
    # mailinglist-verwerking). Voorkomt reply-loops.
    if auto_submitted:
        return True
    fa = (from_addr or "").lower()
    if any(s in fa for s in SPAM_SENDERS):
        return True
    subj = (subject or "").lower()
    if any(h in subj for h in IGNORE_SUBJECT_HINTS if h):
        return True
    if _looks_like_newsletter(subject, body):
        return True
    return False


def _hdr(msg: email.message.Message, name: str) -> str:
    return _dm(msg.get(name))


def fetch_new(
    mailbox_id: str,
    host: str,
    port: int,
    user: str,
    pw: str,
    conn,
    use_ssl: bool = False,
) -> List[Dict]:
    """Haal ongeziene mails voor één mailbox op. Spam, nieuwsbrieven én
    auto-generated mail (out-of-office/bounces) worden als gelezen weg geschreven
    en komen niet terug voor classificatie. `conn` is een geopende sqlite-connectie;
    de caller commit.

    `use_ssl` — wanneer True wordt er POP3_SSL (STARTTLS/TLS, bv. poort 995)
    gebruikt in plaats van kaal POP3. Office365/Exchange en vrijwel alle hosters
    hebben basic-auth op 110 uitgezet; die mailboxen VERPLICHTEN SSL.

    Bewaart threading-headers (Message-ID, In-Reply-To, References) zodat een
    antwoord netjes als reply op de originele mail landt."""
    if use_ssl:
        srv = poplib.POP3_SSL(host, int(port), timeout=20)
    else:
        srv = poplib.POP3(host, int(port), timeout=20)
    try:
        srv.user(user)
        srv.pass_(pw)
        _, items, _ = srv.list()
        _, uidl_lines, _ = srv.uidl()
    except Exception as e:
        srv.quit()
        raise RuntimeError(f"POP3 mislukt voor {user}: {e}")

    uidl_map: Dict[str, str] = {}
    for line in uidl_lines:
        parts = line.decode().split()
        if len(parts) >= 2:
            uidl_map[parts[0]] = parts[1]

    seen = {
        r["uidl"]
        for r in conn.execute(
            "SELECT uidl FROM mail_inbox WHERE mailbox_id=?", (mailbox_id,)
        )
    }

    out: List[Dict] = []
    for num, uidl in uidl_map.items():
        if uidl in seen:
            continue
        _, lines, _ = srv.retr(num)
        msg = email.message_from_bytes(b"\n".join(lines))
        from_addr = _dm(msg["From"])
        from_name = from_addr.split("<")[0].strip().strip('"')
        subject = _dm(msg["Subject"])
        # Auto-submitted? (Out-of-office, bounce, vacation, mailinglist)
        auto_sub = (
            msg.get("Auto-Submitted") is not None
            or msg.get("Precedence") in ("junk", "bulk", "list")
            or "Auto-Submitted" in (msg.get("X-Auto-Response", ""))
        )
        if _should_ignore(from_addr, subject, body="", auto_submitted=auto_sub):
            label = "newsletter" if _looks_like_newsletter(subject, "") else (
                "auto" if auto_sub else "spam")
            conn.execute(
                "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,"
                "classified,message_id,in_reply_to,\"references\",auto_submitted) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (mailbox_id, uidl, _addr_only(from_addr), subject, "",
                 label, _hdr(msg, "Message-ID"), _hdr(msg, "In-Reply-To"),
                 _hdr(msg, "References"), 1 if auto_sub else 0),
            )
            continue
        body = _body(msg)
        cur = conn.execute(
            "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,from_name,subject,body_text,"
            "classified,message_id,in_reply_to,\"references\",auto_submitted) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (mailbox_id, uidl, _addr_only(from_addr), from_name, subject, body, "unknown",
             _hdr(msg, "Message-ID"), _hdr(msg, "In-Reply-To"), _hdr(msg, "References"),
             1 if auto_sub else 0),
        )
        out.append({
            "id": cur.lastrowid,
            "uidl": uidl,
            "from_addr": _addr_only(from_addr),
            "from_name": from_name,
            "subject": subject,
            "body_text": body,
            "message_id": _hdr(msg, "Message-ID"),
            "in_reply_to": _hdr(msg, "In-Reply-To"),
            "references": _hdr(msg, "References"),
        })
    srv.quit()
    return out
