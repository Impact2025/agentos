"""Insert de reserved-word + demo-seed PITFALL-sectie in SKILL.md"""
from pathlib import Path

p = Path(r"C:\Users\v_mun\AppData\Local\hermes\skills\agentos\agentos-helpdesk-knowledge\SKILL.md")
s = p.read_text(encoding="utf-8")

marker = "## PITFALL — run_mailbox vereist de VOLLEDIGE rij (incl. pop_user)"
assert marker in s, "marker niet gevonden"

addendum = '''## PITFALL — mail_reply-kolom `references` (reserved word) en demo-seeding
`references` is een SQL-reserved word. In een INSERT-column-list moet je hem
quoten — de `[brackets]`-stijl is overal helderder en vermijdt dubbele-quotes
die in een Python-string-literale snel door elkaar heen vallen
(`"references"` -> SyntaxError, of stilzwijgende truncation). Voorbeeld:
```sql
INSERT INTO mail_reply (mailbox_id, inbox_id, to_addr, subject, draft_body, edited_body,
                        status, created_at, in_reply_to, [references])
VALUES (?,?,?,?,?,?,?,?,?,?)
```
Een incident in `seed_demo_helpdesk_bvj.py` liet een patch de INSERT falen met
`sqlite3.OperationalError: near "references": syntax error`.

Als de frontend een `Unexpected token 'I', Internal S...`-fout rapporteert
terwijl de backend een 500 text-body terugstelt, is dat vaak de service zelf die
CRASHED (hier: reserve-woord SQL-syntax of een Typeerror in `ac.build_inbox`)
— niet de auth-gate. Verifieer direct door de service aan te roepen in de
venv-python, vóór de HTTP-layer:
```python
from backend.domains.action_center import service as ac
res = ac.build_inbox(project="Bewaard voor Jou")
print(res["counts"])            # {'total':.., 'needs_you':.., 'errors':..}
import json; json.dumps(res)    # moet zonder ValueError lukken
```
Een onvolledige/crashed build_inbox produceert de 500-body die de frontend
probeert te `response.json()`-parsen -> JSON-error. Een 401 met een clean
JSON `{"detail":"..."}` is de normale auth-gate (browser logt die rechtdoor).

Demo-helpdesk seeden zonder auth-gate (template: `seed_demo_helpdesk_bvj.py`
in `D:/APPS/agentos`):
```python
from backend.shared.database import init_db, get_conn
init_db()
with get_conn() as c:
    mb = dict(c.execute("SELECT id,project,address,signature FROM mailboxes WHERE id=?", ("mb_bewaardvoorjou",)).fetchone())
    # 1) inbox-vragen (classified='question')
    # 2) concept-antwoorden (status='pending_review'/'edited')
    # 3) verzonden antwoorden (status='sent') + known_senders upsert
    c.commit()
```
Idiomen: `get_conn()` is een `@contextmanager` (commit/rollback/close in `with`);
uidl is uniek per (mailbox_id, uidl) -> uuid-gedraagde demo-uidl's; dedupe
her-runs via `DELETE ... WHERE subject LIKE '%DEMO_SEED%'` vóór inserts;
gebruik `[references]` én `INSERT OR IGNORE` in `known_senders`. Na seeden:
herstart de dev-server en refres — `ac.build_inbox` toont de nieuwe items
direct én verdwijnt de JSON-error (service crasht niet meer).


'''

s = s.replace(marker, addendum + marker, 1)
p.write_text(s, encoding="utf-8")
print("patched; nieuwe lengte:", len(s))
print("reserved-word sectie present:", "## PITFALL — mail_reply-kolom" in s)
print("run_mailbox nog aanwezig:", marker in s)
