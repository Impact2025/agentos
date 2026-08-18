import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.domains.publish.quality_guard import check

cases = {
  # De 4 vanmorgen geweigerde (moeten NU passen)
  "Ictusgo 'boodschap blijft hangen'": "<p>Er is een situatie waarin de boodschap niet altijd even duidelijk overkomt. Alles hangt ervan af hoe je het brengt.</p>",
  "levensverhaal gids": "<p>Alles over je levensverhaal vastleggen is een mooie manier om herinneringen te bewaren. Open het document en start met de eerste pagina.</p>",
  "cadeaus koppels": "<p>De 10 beste cadeaus voor koppels die echt verbinding willen. Was je op zoek naar inspiratie? Die vind je hier.</p>",
  "buurtinitiatieven": "<p>Buurtinitiatieven meten impact: zo doe je dat in 3 stappen. Per wijk werkt het anders, maar het halfjaarlijkse overzicht helpt.</p>",
  # Echte rot (moet FAILEN)
  "tokenrot EN": "<p>Therefore, however, this is not a valid Dutch text. Without proper language the content fails. The system detected corruption.</p>",
  "tokenrot DE": "<p>Wir haben eine neue Methode, aber es ist sehr schwer. Dieser Ansatz wird nicht funktionieren weil wir keine Zeit haben.</p>",
  # CJK rot (moet FAILEN)
  "CJK": "<p>Dit artikel gaat over 工具 en 数据 maar dat hoort hier niet.</p>",
  # placeholder (moet FAILEN)
  "placeholder": "<p>Lees meer op [link naar toolkit] voor het volledige overzicht.</p>",
  # een los Engels woord in NL (moet PASSEN: ratio < drempel)
  "1 los EN woord": "<p>Er zijn verschillende manieren om dit aan te pakken. De tool helpt je daarbij, maar het blijft mensenwerk.</p>",
  # korte NL zin met was/er/in (moet PASSEN)
  "korte NL": "<p>Het was een mooie dag. Er was veel te doen in de stad.</p>",
}
fail = 0
for name, html in cases.items():
    ok, issues, susp = check(html)
    verdict = "PASS" if ok else "FAIL"
    want = "PASS"
    mark = "OK " if (ok == (want == "PASS")) else "BAD"
    if mark == "BAD":
        fail += 1
    print(f"[{mark}] {verdict} susp={susp:>3}  {name}")
    if not ok:
        print("        ->", "; ".join(issues)[:160])
print("\nBAD cases:", fail)
