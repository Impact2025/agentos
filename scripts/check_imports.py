#!/usr/bin/env python
"""Preventieve import-guard voor de Impact OS backend.

Probleem (18-08-2026): de scheduler-job `_run_facebook_content_ideas_job`
deed `from ...shared.outcomes import log_outcome` — maar `backend/` is al het
top-level package, dus `...` ging *buiten* het package ("attempted relative
import beyond top-level package"). De job crashte pas bij de wekelijkse
catch-up-run, niet bij opstart, en hield de health-banner dagenlang rood.

Deze check vangt die klasse bij elke run:
  - elke top-level module in backend/*.py mag GEEN `from ...` (3+ dots) doen,
    want die bestanden zíjn het top-level package en hebben hoogstens 1 dot nodig.
  - diepere modules (backend/domains/*/) mogen wél 3 dots gebruiken om
    `backend.shared` te bereiken — die worden niet gecontroleerd.

Exit code 0 = schoon, 1 = verboden import gevonden.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"


def main() -> int:
    bad = []
    for py in sorted(BACKEND.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            s = line.lstrip()
            # echte `from ...` statement? (geen commentaar, dat begint met #)
            if s.startswith("from ") and "..." in s and " import " in s:
                bad.append((py.name, i, s.strip()))
    if bad:
        print("VERBODEN import gevonden in top-level backend-modules:")
        for name, ln, src in bad:
            print(f"  {name}:{ln}: {src}")
        print("\nFix: vervang `from ...` door `from .` (backend/ is het top-level package).")
        return 1
    print(f"OK — geen verboden `from ...`-imports in {len(list(BACKEND.glob('*.py')))} top-level backend-modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
