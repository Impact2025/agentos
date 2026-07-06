"""
SMTP e-mailverzending voor financiële rapporten en GA-rapporten.
Ondersteunt STARTTLS (poort 587) en SSL (poort 465).
Stuurt altijd zowel plain-text als HTML (markdown gerenderd).
"""
import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ...shared.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_EMAIL_TO


def is_configured() -> bool:
    return all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD])


def _markdown_to_html(md: str) -> str:
    """Minimale markdown → HTML voor e-mailclients (geen externe deps)."""
    lines = md.split("\n")
    html_lines = []
    in_table = False
    in_list = False

    for line in lines:
        # Tabellen
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                html_lines.append('<table style="border-collapse:collapse;width:100%;margin:8px 0">')
                in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue  # separator-rij
            tag = "th" if not any(
                "|" in prev and "---" in prev for prev in html_lines[-3:]
            ) else "td"
            row = "".join(
                f'<{tag} style="border:1px solid #334155;padding:6px 10px">{c}</{tag}>'
                for c in cells
            )
            html_lines.append(f"<tr>{row}</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # Koppen
        if line.startswith("### "):
            html_lines.append(f'<h3 style="color:#818cf8;margin:1em 0 0.3em">{line[4:]}</h3>')
        elif line.startswith("## "):
            html_lines.append(f'<h2 style="color:#a5b4fc;margin:1.2em 0 0.4em">{line[3:]}</h2>')
        elif line.startswith("# "):
            html_lines.append(f'<h1 style="color:#e2e8f0;margin:1.2em 0 0.4em">{line[2:]}</h1>')
        # Lijstitems
        elif line.startswith("- ") or line.startswith("* "):
            if not in_list:
                html_lines.append('<ul style="padding-left:1.4em;margin:0.4em 0">')
                in_list = True
            content = _inline(line[2:])
            html_lines.append(f'<li style="margin-bottom:3px">{content}</li>')
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if line.strip() == "":
                html_lines.append("<br>")
            elif line.startswith("---"):
                html_lines.append('<hr style="border:none;border-top:1px solid #334155;margin:12px 0">')
            else:
                html_lines.append(f'<p style="margin:0.3em 0;line-height:1.6">{_inline(line)}</p>')

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="background:#0b0f1a;color:#e2e8f0;font-family:system-ui,sans-serif;
             max-width:760px;margin:0 auto;padding:24px;font-size:15px;line-height:1.6">
{body}
<p style="margin-top:32px;padding-top:16px;border-top:1px solid #334155;
   color:#475569;font-size:12px">
  Agent OS · Finance Expert · automatisch gegenereerd
</p>
</body>
</html>"""


def _inline(text: str) -> str:
    """Verwerk inline markdown (bold, italic, code, links)."""
    text = re.sub(r"\*\*(.+?)\*\*", r'<strong>\1</strong>', text)
    text = re.sub(r"\*(.+?)\*", r'<em>\1</em>', text)
    text = re.sub(r"`(.+?)`", r'<code style="background:#1e293b;color:#a5b4fc;padding:1px 5px;border-radius:3px">\1</code>', text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2" style="color:#818cf8">\1</a>', text)
    return text


def send_report(subject: str, body: str, to: str = None) -> bool:
    if not is_configured():
        return False

    recipient = to or REPORT_EMAIL_TO
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = recipient

    # Plain text als fallback, HTML als voorkeur
    plain = body
    # Body begint met "subject\n===..." — strip die header voor HTML
    md_body = re.sub(r"^.+\n={3,}\n\n", "", body, count=1)
    html = _markdown_to_html(md_body)

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, recipient, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, recipient, msg.as_string())
        return True
    except Exception as e:
        print(f"[Email] Versturen mislukt: {e}")
        return False
