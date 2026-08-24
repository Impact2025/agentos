"""Minimale markdown → HTML-renderer voor e-mail, gedeeld door alle verzendproviders
(SMTP via email_service.py, Resend via resend_service.py). Losgetrokken uit
email_service.py zodat providers elkaar niet hoeven te importeren.

Licht, zakelijk template — geen dark-mode/code-editor-palet, geen emoji's in de
opmaak zelf. Content (de markdown-body) bepaalt eigen woordkeuze; dit bestand
gaat alleen over de vormgeving eromheen.
"""
import re

_INK = "#111827"       # koppen
_TEXT = "#374151"      # lopende tekst
_MUTE = "#6b7280"      # bijschrift / footer
_BORDER = "#e5e7eb"
_ACCENT = "#4f46e5"    # links, accentlijn


def to_html(md: str, brand: str = "Iris") -> str:
    """Minimale markdown → HTML voor e-mailclients (geen externe deps)."""
    lines = md.split("\n")
    html_lines = []
    in_table = False
    in_list = False

    for line in lines:
        # Tabellen
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                html_lines.append(
                    f'<table style="border-collapse:collapse;width:100%;margin:16px 0;'
                    f'font-size:14px">')
                in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue  # separator-rij
            tag = "th" if not any(
                "|" in prev and "---" in prev for prev in html_lines[-3:]
            ) else "td"
            style = (
                f'text-align:left;border-bottom:1px solid {_BORDER};padding:8px 12px;'
                + (f'color:{_INK};font-weight:600;background:#f9fafb' if tag == "th"
                   else f'color:{_TEXT}')
            )
            row = "".join(f'<{tag} style="{style}">{c}</{tag}>' for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # Koppen
        if line.startswith("### "):
            html_lines.append(
                f'<h3 style="color:{_INK};font-size:15px;font-weight:600;'
                f'margin:1.4em 0 0.4em">{line[4:]}</h3>')
        elif line.startswith("## "):
            html_lines.append(
                f'<h2 style="color:{_INK};font-size:17px;font-weight:600;'
                f'margin:1.6em 0 0.5em">{line[3:]}</h2>')
        elif line.startswith("# "):
            html_lines.append(
                f'<h1 style="color:{_INK};font-size:20px;font-weight:700;'
                f'margin:0 0 0.6em">{line[2:]}</h1>')
        # Lijstitems
        elif line.startswith("- ") or line.startswith("* "):
            if not in_list:
                html_lines.append('<ul style="padding-left:1.3em;margin:0.5em 0">')
                in_list = True
            content = _inline(line[2:])
            html_lines.append(
                f'<li style="margin-bottom:4px;color:{_TEXT}">{content}</li>')
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if line.strip() == "":
                html_lines.append("<div style=\"height:8px\"></div>")
            elif line.startswith("---"):
                html_lines.append(
                    f'<hr style="border:none;border-top:1px solid {_BORDER};margin:20px 0">')
            else:
                html_lines.append(
                    f'<p style="margin:0.4em 0;line-height:1.6;color:{_TEXT}">'
                    f'{_inline(line)}</p>')

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;background:#ffffff;border:1px solid {_BORDER};border-radius:8px">
<tr><td style="padding:24px 32px 16px;border-bottom:1px solid {_BORDER}">
  <span style="font-size:13px;font-weight:700;letter-spacing:0.06em;color:{_ACCENT};
               text-transform:uppercase">{brand}</span>
</td></tr>
<tr><td style="padding:28px 32px;font-size:14px">
{body}
</td></tr>
<tr><td style="padding:16px 32px 24px;border-top:1px solid {_BORDER}">
  <p style="margin:0;color:{_MUTE};font-size:12px">Automatisch gegenereerd door Impact OS</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _inline(text: str) -> str:
    """Verwerk inline markdown (bold, italic, code, links)."""
    text = re.sub(r"\*\*(.+?)\*\*", rf'<strong style="color:{_INK}">\1</strong>', text)
    text = re.sub(r"\*(.+?)\*", r'<em>\1</em>', text)
    text = re.sub(
        r"`(.+?)`",
        r'<code style="background:#f3f4f6;color:#374151;padding:1px 5px;'
        r'border-radius:3px;font-size:0.9em">\1</code>', text)
    text = re.sub(
        r"\[(.+?)\]\((.+?)\)",
        rf'<a href="\2" style="color:{_ACCENT};text-decoration:none">\1</a>', text)
    return text


def strip_header(body: str) -> str:
    """Verwijder de 'subject\\n===...\\n\\n'-header die rapportteksten vaak vooraf laten gaan."""
    return re.sub(r"^.+\n={3,}\n\n", "", body, count=1)
