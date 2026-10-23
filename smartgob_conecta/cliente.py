"""
Cliente HTTP de SmartGob Conecta: enviar texto y descargar audios recibidos.
============================================================================

Asíncrono (httpx.AsyncClient) porque lo usa el canal FastAPI desde el event loop.
Todas las URLs salen de `SMARTGOB_API_URL`: nunca se descarga nada de una URL que
venga en el webhook, así que no hay superficie de SSRF.
"""

import asyncio
import logging
import uuid

import httpx

from . import config

logger = logging.getLogger(__name__)

# Si el audio todavía no está guardado cuando llega el webhook, se reintenta así.
ESPERA_MEDIA_INTENTOS = 5
ESPERA_MEDIA_SEGUNDOS = 2.0


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.SMARTGOB_API_KEY}"}


def _destino(telefono: str) -> str:
    """`to` acepta 09XXXXXXXX o +5939XXXXXXXX. Guardamos solo dígitos con 593."""
    return f"+{telefono}" if telefono.startswith("593") else telefono


def _sin_variables(texto: str) -> str:
    """Conecta reemplaza `{{variable}}` en el servidor y rechaza variables que no
    conoce. Una respuesta del LLM nunca debe usar esa sintaxis, pero si la usara el
    envío fallaría con 400: se rompe la doble llave."""
    return texto.replace("{{", "{ {").replace("}}", "} }")


async def _post_mensaje(destino: dict, texto: str) -> httpx.Response | None:
    cuerpo = {"connectionId": config.SMARTGOB_CONNECTION_ID, **destino,
              "text": _sin_variables(texto)[:4096]}   # 4096: límite del API
    headers = {**_headers(), "Idempotency-Key": f"agente:{uuid.uuid4().hex}"}
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_SEGUNDOS) as cliente:
            return await cliente.post(f"{config.SMARTGOB_API_URL}/messages", headers=headers,
                                      json=cuerpo)
    except httpx.HTTPError as e:
        logger.error("Conecta POST /messages falló: %s", type(e).__name__)
        return None


async def enviar_texto(telefono: str, texto: str, contact_id: str | None = None) -> bool:
    """Envía `texto` al `telefono`. True si Conecta registró el envío (202).

    POST /messages con `to` solo acepta números de Ecuador (09… / +5939…): a un +51
    responde 400 INVALID_PHONE. Si el webhook trajo el `contactId` de la persona, se
    reintenta enviando al contacto (la API acepta `contactId` en lugar de `to`).
    """
    r = await _post_mensaje({"to": _destino(telefono)}, texto)
    if r is not None and r.status_code == 400 and "INVALID_PHONE" in r.text and contact_id:
        logger.info("Número rechazado (INVALID_PHONE); reintento por contactId.")
        r = await _post_mensaje({"contactId": contact_id}, texto)
    if r is None:
        return False
    if r.status_code == 409:
        # Destinatario con baja de comunicaciones o capacidad no disponible.
        logger.warning("Conecta rechazó el envío (409): %s", r.text[:300])
        return False
    if r.status_code == 400 and "INVALID_PHONE" in r.text:
        logger.error("Conecta rechazó el número (INVALID_PHONE): POST /messages solo acepta "
                     "números de Ecuador (09… / +5939…)%s.",
                     ", ni siquiera por contactId" if contact_id else " y no llegó contactId")
        return False
    if r.status_code >= 400:
        logger.error("Conecta POST /messages respondió %s: %s", r.status_code, r.text[:300])
        return False
    return True


async def _media_file_id(mensaje_id: str, cliente: httpx.AsyncClient) -> str | None:
    """Espera a que el audio quede guardado (mediaState STORED) y devuelve su id."""
    for intento in range(ESPERA_MEDIA_INTENTOS):
        r = await cliente.get(f"{config.SMARTGOB_API_URL}/messages/{mensaje_id}",
                              headers=_headers())
        r.raise_for_status()
        datos = r.json()
        if datos.get("mediaFileId"):
            return datos["mediaFileId"]
        if datos.get("mediaState") in ("FAILED", "EXPIRED"):
            return None
        await asyncio.sleep(ESPERA_MEDIA_SEGUNDOS)
    return None


async def descargar_media(media_file_id: str | None, mensaje_id: str | None) -> tuple[bytes, str]:
    """Bytes y mimetype de un audio recibido. Lanza excepción si no se puede obtener."""
    async with httpx.AsyncClient(timeout=config.TIMEOUT_SEGUNDOS) as cliente:
        if not media_file_id and mensaje_id:
            media_file_id = await _media_file_id(mensaje_id, cliente)
        if not media_file_id:
            raise ValueError("El mensaje no tiene un archivo guardado (mediaFileId)")
        r = await cliente.get(f"{config.SMARTGOB_API_URL}/files/{media_file_id}/content",
                              headers=_headers())
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "audio/ogg")
