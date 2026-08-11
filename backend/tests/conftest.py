"""Test-config: zet backend/ op sys.path zodat `import domains...` werkt

De app importeert domeinen via relatieve imports (from .domains...), maar de
test-suite gebruikt absolute imports (from domains...) zoals in de rest van de
codebase gebruikelijk is. Deze conftest zorgt dat backend/ op het Python-pad
staat vóórdat de testmodules geladen worden.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
