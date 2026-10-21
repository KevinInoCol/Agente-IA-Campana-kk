"""
Paquete `debounce`: buffer + debounce de mensajes con Redis.
==================================================================

Concatena los mensajes que un contacto envía en ráfaga (dentro de una ventana de
silencio) en UNA sola entrada al agente, evitando respuestas duplicadas y
descontextualizadas. Los mensajes se guardan en Redis SOLO durante la interacción
(memoria efímera) y se eliminan una vez el agente responde.

API pública:
    - encolar_mensaje(conversation_id, mensaje, procesar)  -> encola y agenda el flush
    - BUFFER_ENABLED / BUFFER_WINDOW_SECONDS / BUFFER_SEPARATOR  -> config del debounce
    - REDIS_KEY_PREFIX / KEY_PREFIX_GENERICO  -> namespace de las claves (aísla tenants)
    - close_redis()  -> cierra el cliente Redis en el shutdown del canal

El canal (`main_conecta.py`) es el ÚNICO que integra esto; el resto del agente
no sabe que existe un buffer.
"""

from .buffer import encolar_mensaje
from .config import (
    BUFFER_ENABLED,
    BUFFER_SEPARATOR,
    BUFFER_WINDOW_SECONDS,
    KEY_PREFIX_GENERICO,
    REDIS_KEY_PREFIX,
)
from .redis_client import close_redis, get_redis

__all__ = [
    "encolar_mensaje",
    "close_redis",
    "get_redis",
    "BUFFER_ENABLED",
    "BUFFER_WINDOW_SECONDS",
    "BUFFER_SEPARATOR",
    "REDIS_KEY_PREFIX",
    "KEY_PREFIX_GENERICO",
]
