"""Researcher-domein package."""
from .service import get_service, ResearcherService
from . import router

__all__ = ["get_service", "ResearcherService", "router"]
