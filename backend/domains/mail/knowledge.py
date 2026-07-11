"""Projectkennis voor de helpdesk-drafter — gelaagd, zodat elk antwoord klinkt
alsof het van iemand komt die het project door en door kent.

Lagen (elke laag is optioneel; wat er is, gaat mee):
  1. Vault-notes    — 10_Projects/{project}/ + WeAreImpact core (wie is de maker).
  2. Site-profiel   — `sites.profile` + CTA's + base_url (wie/wat/doelgroep/toon,
                      dezelfde kennisbank die de contentschrijvers voedt).
  3. Live pagina's  — echte URL's uit `published_pages`, zodat de drafter naar
                      bestaande pagina's kan linken i.p.v. "[jouw domein]"-placeholders.
  4. Geleerde Q&A   — de laatst verstuurde (door Vincent goedgekeurde) antwoorden
                      van déze mailbox als voorbeelden: zo leert de helpdesk van
                      elke goedkeuring en vooral van elke handmatige correctie.

Daarnaast: `thread_history` geeft de eerdere wisseling met déze afzender terug,
zodat een "Re: RE:"-mail niet als losse eerste vraag wordt beantwoord.
"""
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Ruime maar begrensde budgetten — de drafter draait op een klein/snel model.
_MAX_SECTION_CHARS = 4000
_MAX_HISTORY_CHARS = 3000
_MAX_QA_EXAMPLES = 4
_MAX_LIVE_PAGES = 12


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def _site_for_project(conn, project: str) -> Optional[Dict]:
    """Zelfde naam-matching als de goal-executor: sites.name ~ projectnaam.
    Leest via de meegegeven conn (dezelfde transactie als de mail-run)."""
    try:
        rows = conn.execute("SELECT * FROM sites").fetchall()
        return next(
            (dict(r) for r in rows if _norm(r["name"]) == _norm(project)), None
        )
    except Exception as e:
        logger.warning("Site-lookup voor helpdesk-kennis mislukt: %s", e)
        return None


def _clip(text: str, limit: int = _MAX_SECTION_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n[…ingekort]"


def _vault_section(project: str) -> str:
    """Laag 1: Obsidian-vault (single source of truth over het project + de maker)."""
    try:
        from ...shared.vault_reader import VaultReader
        vr = VaultReader()
        if not vr.is_configured:
            return ""
        # Per-note clippen: zo blijft elke notitie behouden en wordt alleen
        # een te lange notitie zélf ingekort — i.p.v. de hele blob bij 4000
        # tekens af te kappen (wat de laatste notities stilzwijgend weggooide).
        parts = []
        proj = vr.get_project_folder_notes(project)
        if proj:
            # get_project_folder_notes leverd "## {stem}\n{text}" per bestand op;
            # splits per kop en clip elke sectie los.
            for block in proj.split("\n## "):
                block = block.strip()
                if not block:
                    continue
                # eerste blok heeft geen "## "-prefix meer na de split
                if not block.startswith("#"):
                    block = "## " + block
                parts.append(_clip(block))
        if project and project.lower() != "weareimpact":
            core = vr.get_core_context("WeAreImpact")
            if core:
                parts.append(
                    f"# Over de maker (WeAreImpact / Vincent van Munster)\n{_clip(core)}"
                )
        return "\n\n".join(p for p in parts if p)
    except Exception as e:
        logger.warning("Kon vault-kennis niet laden: %s", e)
        return ""


def _site_section(site: Dict) -> str:
    """Laag 2: site-profiel + CTA's + base_url uit de SEO-kennisbank."""
    lines = []
    base = (site.get("base_url") or "").strip().rstrip("/")
    if base:
        lines.append(f"Website: {base}")
    profile = (site.get("profile") or "").strip()
    if profile:
        lines.append(_clip(profile, 2500))
    try:
        ctas = json.loads(site.get("ctas") or "[]")
        ctas = [str(c).strip() for c in ctas if str(c).strip()] if isinstance(ctas, list) else []
    except json.JSONDecodeError:
        ctas = []
    if ctas:
        lines.append("Call-to-actions die passen bij dit merk:\n- " + "\n- ".join(ctas[:6]))
    if not lines:
        return ""
    return f"# Merkprofiel {site.get('name', '')}\n" + "\n\n".join(lines)


def _live_pages_section(conn, site_id: str) -> str:
    """Laag 3: echte live URL's — dé remedie tegen '[jouw domein]'-placeholders."""
    rows = conn.execute(
        "SELECT title, url FROM published_pages WHERE site_id=? AND url != '' "
        "ORDER BY updated_at DESC LIMIT ?",
        (site_id, _MAX_LIVE_PAGES),
    ).fetchall()
    if not rows:
        return ""
    lines = [f"- {r['title'] or r['url']}: {r['url']}" for r in rows]
    return (
        "# Live pagina's (dit zijn de ENIGE URL's die je mag noemen — "
        "verzin nooit een andere link of placeholder)\n" + "\n".join(lines)
    )


def _learned_qa_section(conn, mailbox_id: str) -> str:
    """Laag 4: recent verstuurde antwoorden van deze mailbox als voorbeelden.

    `edited_body` gaat vóór `draft_body`: dat is de versie mét Vincents
    correcties — precies wat de drafter moet imiteren."""
    rows = conn.execute(
        "SELECT i.subject AS q_subject, i.body_text AS q_body, "
        "COALESCE(NULLIF(r.edited_body, ''), r.draft_body) AS answer "
        "FROM mail_reply r JOIN mail_inbox i ON i.id=r.inbox_id "
        "WHERE r.mailbox_id=? AND r.status='sent' "
        "ORDER BY r.sent_at DESC LIMIT ?",
        (mailbox_id, _MAX_QA_EXAMPLES),
    ).fetchall()
    if not rows:
        return ""
    parts = []
    for r in rows:
        q = _clip((r["q_body"] or "").strip(), 400)
        a = _clip((r["answer"] or "").strip(), 700)
        if not a:
            continue
        parts.append(f"Vraag: {r['q_subject']}\n{q}\n\nGoedgekeurd antwoord:\n{a}")
    if not parts:
        return ""
    return (
        "# Zo beantwoorden wij vragen (eerder door een mens goedgekeurde antwoorden — "
        "imiteer deze toon en aanpak)\n\n" + "\n\n---\n\n".join(parts)
    )


def build_knowledge(conn, project: str, mailbox: Dict) -> str:
    """Alle kennislagen samen, klaar voor de drafter-systeemprompt."""
    sections = []
    vault = _vault_section(project)
    if vault:
        sections.append(vault)
    site = _site_for_project(conn, project)
    if site:
        s = _site_section(site)
        if s:
            sections.append(s)
        try:
            p = _live_pages_section(conn, site["id"])
            if p:
                sections.append(p)
        except Exception as e:
            logger.warning("Live-pagina's laden mislukt: %s", e)
    try:
        qa = _learned_qa_section(conn, mailbox.get("id", ""))
        if qa:
            sections.append(qa)
    except Exception as e:
        logger.warning("Geleerde Q&A laden mislukt: %s", e)
    # Handmatige extra context op de mailbox-rij blijft gewoon meedoen.
    extra = (mailbox.get("brand_context") or "").strip()
    if extra and extra.lower() != _norm(project):
        sections.append(f"# Extra context van Vincent\n{_clip(extra, 1500)}")
    return "\n\n".join(sections)


def coverage(conn, project: str, mailbox: Dict) -> Dict:
    """Meetbaar antwoord op "weet de helpdesk genoeg?" — per kennislaag of hij
    gevuld is, plus concrete hints om lege lagen te vullen. Voedt het
    'Wat weet de helpdesk?'-paneel in de UI."""
    vault = _vault_section(project)
    site = _site_for_project(conn, project)
    profile_ok, ctas_n, base_url = False, 0, ""
    live_n = 0
    if site:
        base_url = (site.get("base_url") or "").strip()
        profile_ok = bool((site.get("profile") or "").strip())
        try:
            ctas = json.loads(site.get("ctas") or "[]")
            ctas_n = len([c for c in ctas if str(c).strip()]) if isinstance(ctas, list) else 0
        except json.JSONDecodeError:
            ctas_n = 0
        live_n = conn.execute(
            "SELECT COUNT(*) AS n FROM published_pages WHERE site_id=? AND url != ''",
            (site["id"],),
        ).fetchone()["n"]
    qa_n = conn.execute(
        "SELECT COUNT(*) AS n FROM mail_reply WHERE mailbox_id=? AND status='sent'",
        (mailbox.get("id", ""),),
    ).fetchone()["n"]
    signature_ok = bool((mailbox.get("signature") or "").strip())

    hints = []
    if not vault:
        hints.append(f"Geen vault-notes gevonden — maak/vul 10_Projects/{project}/ in Obsidian.")
    if not site:
        hints.append("Geen site gekoppeld — voeg dit project toe onder Sites (zelfde naam als het project).")
    else:
        if not profile_ok:
            hints.append("Site-profiel is leeg — vul 'profile' (wie/wat/doelgroep/toon) bij de site-instellingen.")
        if not base_url:
            hints.append("Site heeft geen base_url — zonder die weet de helpdesk het webadres niet.")
        if live_n == 0:
            hints.append("Nog geen live pagina's bekend — de helpdesk kan geen echte links geven; publiceer via de Wachtrij.")
    if qa_n == 0:
        hints.append("Nog geen verstuurde antwoorden — na je eerste goedkeuringen gaat de helpdesk jouw stijl imiteren.")
    if not signature_ok:
        hints.append("Geen handtekening ingesteld — klanten krijgen nu de generieke Agent OS-footer.")

    return {
        "vault": bool(vault),
        "vault_chars": len(vault),
        "site_found": bool(site),
        "site_profile": profile_ok,
        "base_url": base_url,
        "ctas": ctas_n,
        "live_pages": live_n,
        "learned_qa": qa_n,
        "signature": signature_ok,
        "total_chars": len(build_knowledge(conn, project, mailbox)),
        "hints": hints,
    }


def thread_history(conn, mailbox_id: str, from_addr: str, exclude_inbox_id: int) -> str:
    """Eerdere wisseling met déze afzender op déze mailbox, oud → nieuw.

    Neemt zowel wat de klant eerder schreef als wat wij terugstuurden mee,
    zodat een vervolg-mail ("Re: RE: …") in context wordt beantwoord."""
    if not from_addr:
        return ""
    events = []
    for r in conn.execute(
        "SELECT id, subject, body_text, created_at FROM mail_inbox "
        "WHERE mailbox_id=? AND from_addr=? AND id != ? AND classified='question' "
        "ORDER BY created_at DESC LIMIT 4",
        (mailbox_id, from_addr, exclude_inbox_id),
    ):
        events.append((r["created_at"] or "", "KLANT",
                       f"{r['subject']}\n{_clip((r['body_text'] or '').strip(), 600)}"))
    for r in conn.execute(
        "SELECT COALESCE(NULLIF(edited_body, ''), draft_body) AS body, subject, sent_at "
        "FROM mail_reply WHERE mailbox_id=? AND to_addr=? AND status='sent' "
        "ORDER BY sent_at DESC LIMIT 4",
        (mailbox_id, from_addr),
    ):
        events.append((r["sent_at"] or "", "WIJ",
                       f"{r['subject']}\n{_clip((r['body'] or '').strip(), 600)}"))
    if not events:
        return ""
    events.sort(key=lambda e: e[0])
    lines = [f"[{who}] {txt}" for _, who, txt in events]
    out = "\n\n".join(lines)
    if len(out) > _MAX_HISTORY_CHARS:
        out = out[-_MAX_HISTORY_CHARS:]
    return out
