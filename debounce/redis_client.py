"""
Cliente Redis asíncrono (singleton) para el buffer de mensajes.
===============================================================

Único lugar que conoce CÓMO conectarse a Redis. Resuelve la conexión igual que
el resto del proyecto resuelve Qdrant/Postgres (decisión de arquitectura #6):
si `REDIS_URL` está definida se usa (con prioridad); si no, cae a las piezas
`REDIS_HOST/PORT/DB/PASSWORD/TLS`.

Se usa `redis.asyncio` (no el cliente síncrono) porque el buffer vive dentro del
event loop de FastAPI (`main_conecta.py`): las operaciones a Redis deben ser
`await`-ables para no bloquear el loop mientras se espera la ventana de debounce.
El cliente de redis-py ya gestiona su propio pool de conexiones internamente.
"""

import threading

import redis.asyncio as redis

from . import config

_client: "redis.Redis | None" = None
_lock = threading.Lock()


def get_redis() -> "redis.Redis":
    """Devuelve el cliente Redis asíncrono (creándolo la primera vez).

    `decode_responses=True` para que `lrange`/`get` devuelvan `str` y se puedan
    concatenar directamente (sin `bytes`). El cliente es thread-safe y mantiene
    su pool interno, así que un único singleton sirve para todo el proceso.
    """
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:  # otro hilo pudo crearlo mientras esperábamos
            return _client

        if config.REDIS_URL:
            # rediss:// activa TLS automáticamente.
            _client = redis.from_url(config.REDIS_URL, decode_responses=True)
        else:
            _client = redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                password=config.REDIS_PASSWORD,
                ssl=config.REDIS_TLS,
                decode_responses=True,
            )
        return _client


async def close_redis() -> None:
    """Cierra el cliente/pool de Redis (para el shutdown del canal FastAPI).

    Idempotente: si nunca se creó o ya se cerró, no hace nada.
    """
    global _client
    cliente = _client
    _client = None
    if cliente is not None:
        try:
            await cliente.aclose()
        except Exception as e:  # noqa: BLE001 — cierre best-effort
            print(f"⚠️ Nota al cerrar el cliente de Redis: {e}")
