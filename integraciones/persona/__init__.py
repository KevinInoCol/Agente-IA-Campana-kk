"""Consulta al registro civil (DINARDAP) para corroborar que una cédula existe."""

from .adaptador import consultar_persona, esta_activa, PERSONA_FUENTE

__all__ = ["consultar_persona", "esta_activa", "PERSONA_FUENTE"]
