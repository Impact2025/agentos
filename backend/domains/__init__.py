# Package marker — maakt backend.domains een reguliere package zodat
# relatieve imports (bijv. `from ...shared.database import get_conn` in
# backend/domains/seo/feedback.py) correct resolven naar backend.shared
# in plaats van backend.domains.shared.
