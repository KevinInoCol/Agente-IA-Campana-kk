"""
Buffer + debounce de mensajes entrantes (Redis).
=================================================

Acumula los mensajes que un mismo contacto envía en ráfaga y, tras una ventana
de silencio (`BUFFER_WINDOW_SECONDS`), los concatena en UNA sola entrada para el
agente -> UNA sola respuesta. Evita que el bot conteste mensaje por mensaje.

Mecanismo (debounce por secuencia), por cada mensaje entrante:

    1. `RPUSH` del mensaje a una lista en Redis + `INCR` de un contador de
       secuencia. El mensaje captura su número (`mi_seq`).
    2. Se programa una tarea que ESPERA la ventana.
    3. Al despertar, solo procesa si su `mi_seq` sigue siendo el último: si llegó
       un mensaje más nuevo, aborta (esa tarea, más reciente, se encargará). Así
       "gana" el último mensaje y no hay respuestas duplicadas.
    4. La tarea ganadora hace `LRANGE`, une los mensajes y llama al agente UNA vez.

Limpieza (SIEMPRE tras responder, en `finally`): la concatenación sirve para UNA
sola inferencia (memoria efímera por interacción). Si no llegó nada nuevo, se
BORRA todo el buffer; si llegaron mensajes durante la respuesta, se conservan solo
esos (`LTRIM`) para la próxima inferencia.

Claves en Redis (por conversación):
    <prefijo>:buffer:msgs:{conv} -> lista de mensajes acumulados
    <prefijo>:buffer:seq:{conv}  -> contador de secuencia (debounce)
"""

import asyncio
import logging
from typing import Awaitable, Callable

from . import config
from .redis_client import get_redis

logger = logging.getLogger("debounce")

# Callback que el canal implementa: recibe (conversation_id, mensaje_concatenado),
# invoca al agente y envía la respuesta. Es async.
Procesador = Callable[[str, str], Awaitable[None]]

# Referencias fuertes a las tareas en vuelo: sin esto el GC podría recolectar una
# `asyncio.Task` en segundo plano y cancelar el debounce a mitad de camino.
_tasks: "set[asyncio.Task]" = set()


def _msgs_key(conversation_id: str) -> str:
    return f"{config.REDIS_KEY_PREFIX}:buffer:msgs:{conversation_id}"


def _seq_key(conversation_id: str) -> str:
    return f"{config.REDIS_KEY_PREFIX}:buffer:seq:{conversation_id}"


async def encolar_mensaje(
    conversation_id: str, mensaje: str, procesar: Procesador
) -> int:
    """Encola un mensaje entrante y programa su flush con debounce.

    Devuelve el número de secuencia asignado a este mensaje (útil para tests/logs).
    El handler del webhook debe retornar de inmediato tras llamar a esta función
    (la respuesta se envía luego, dentro de `procesar`, al vencer la ventana).
    """
    r = get_redis()
    msgs_key = _msgs_key(conversation_id)
    seq_key = _seq_key(conversation_id)

    # RPUSH + INCR + refresco de TTL, atómico (una sola ida y vuelta a Redis).
    async with r.pipeline(transaction=True) as pipe:
        pipe.rpush(msgs_key, mensaje)
        pipe.incr(seq_key)
        pipe.expire(msgs_key, config.BUFFER_TTL_SECONDS)
        pipe.expire(seq_key, config.BUFFER_TTL_SECONDS)
        resultados = await pipe.execute()
    mi_seq = int(resultados[1])

    tarea = asyncio.create_task(
        _esperar_y_procesar(conversation_id, mi_seq, procesar)
    )
    _tasks.add(tarea)
    tarea.add_done_callback(_tasks.discard)
    return mi_seq


async def _esperar_y_procesar(
    conversation_id: str, mi_seq: int, procesar: Procesador
) -> None:
    """Espera la ventana de silencio y, si nadie escribió después, concatena y responde."""
    r = get_redis()
    msgs_key = _msgs_key(conversation_id)
    seq_key = _seq_key(conversation_id)

    await asyncio.sleep(config.BUFFER_WINDOW_SECONDS)

    # ¿Sigo siendo la última tarea? Si no, un mensaje más nuevo se encarga.
    seq_actual = await r.get(seq_key)
    if seq_actual is None or int(seq_actual) != mi_seq:
        return

    mensajes = await r.lrange(msgs_key, 0, -1)
    if not mensajes:
        return
    combinado = config.BUFFER_SEPARATOR.join(mensajes)
    n = len(mensajes)

    # Log sin PII: solo el conteo de mensajes y la secuencia (no el contenido ni
    # el conversation_id, que puede ser el teléfono del contacto).
    logger.info("Flush del buffer: mensajes concatenados=%d | seq=%d", n, mi_seq)

    try:
        await procesar(conversation_id, combinado)
    finally:
        # Limpieza SIEMPRE (responda bien o falle), a prueba de carreras:
        seq_final = await r.get(seq_key)
        if seq_final is not None and int(seq_final) == mi_seq:
            # Nada nuevo llegó mientras respondíamos -> borrar todo el buffer.
            await r.delete(msgs_key, seq_key)
        else:
            # Llegaron mensajes nuevos -> quitar solo los ya respondidos (los
            # primeros n) y conservar los nuevos para la próxima inferencia.
            await r.ltrim(msgs_key, n, -1)
