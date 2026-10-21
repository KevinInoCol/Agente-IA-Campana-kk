"""
Integración con SmartGob Conecta (WhatsApp del candidato).
==========================================================

El canal (`main_conecta.py`) es el único que usa este paquete; el agente no sabe
por dónde llegan los mensajes.
"""

from .cliente import descargar_media, enviar_texto
from .config import esta_configurado
from .webhook import (
    EVENTO_MENSAJE,
    EVENTO_PRUEBA,
    MensajeEntrante,
    firma_valida,
    forma_del_payload,
    parsear,
    tipo_de_evento,
)

__all__ = [
    "EVENTO_MENSAJE",
    "EVENTO_PRUEBA",
    "MensajeEntrante",
    "descargar_media",
    "enviar_texto",
    "esta_configurado",
    "firma_valida",
    "forma_del_payload",
    "parsear",
    "tipo_de_evento",
]
