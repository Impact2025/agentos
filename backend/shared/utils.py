"""Gedeelde utility-functies."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def now() -> str:
    """ISO-timestamp voor database-opslag."""
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    """Maak een URL-vriendelijke slug van een willekeurige string."""
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "project"
