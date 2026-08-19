"""Parser-test: varianten waarin het LLM de 4 scènes kan aanleveren."""
import sys

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared.blog_video import _parse_script  # noqa: E402

CASES = {
    "newlines": "HOOK: A een.\nBODY: B twee.\nBODY: C drie.\nCTA: D vier.",
    "trailing-spaces": "HOOK: A een.  \nBODY: B twee.  \nBODY: C drie.  \nCTA: D vier.",
    "blank-lines": "HOOK: A een.\n\nBODY: B twee.\n\nBODY: C drie.\n\nCTA: D vier.",
    "markdown": "**HOOK:** A een.\n- **BODY:** B twee.\n- **BODY:** C drie.\n**CTA:** D vier.",
    "one-line": "HOOK: A een. BODY: B twee. BODY: C drie. CTA: D vier.",
    "genummerd": "1. HOOK: A een.\n2. BODY: B twee.\n3. BODY: C drie.\n4. CTA: D vier.",
}

fails = 0
for name, raw in CASES.items():
    scenes = _parse_script(raw, "Titel")
    kinds = [s.kind for s in scenes]
    ok = kinds == ["hook", "body", "body", "cta"]
    fails += 0 if ok else 1
    print(("OK  " if ok else "FAIL"), name, kinds, [s.narration for s in scenes])
print("\nmislukt:", fails)
sys.exit(1 if fails else 0)
