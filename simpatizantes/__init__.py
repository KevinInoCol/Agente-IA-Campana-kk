"""Persistencia de ciudadanos registrados y simpatizantes (Postgres + Apps Script opcional)."""

from . import apps_script, repositorio
from .repositorio import Ciudadano, Simpatizante

__all__ = ["apps_script", "repositorio", "Ciudadano", "Simpatizante"]
