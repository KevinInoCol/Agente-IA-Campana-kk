"""
Firma y parseo del webhook de SmartGob Conecta (evento `message.received`).
============================================================================

Funciones puras, sin red: se prueban sin Conecta ni FastAPI.

FIRMA. `X-Conecta-Signature: t=<unix>,v1=<hex>` con v1 = HMAC-SHA256(secreto,
"<t>.<cuerpo crudo>"). Se verifica sobre los BYTES crudos del cuerpo (re-serializar
el JSON cambiaría espacios u orden y la firma no coincidiría) y se rechazan marcas de
tiempo de más de 5 minutos (anti-replay).

PAYLOAD. La documentación de Conecta no publica el JSON de `message.received`. El
formato REAL, visto con el primer mensaje de WhatsApp el 2026-10-21, es:

    {"id", "type": "message.received", "createdAt",
     "data": {"from", "text", "hasMedia", "contactId", "messageId",
              "messageType", "connectionId", "conversationId"}}

(y el evento de prueba: {"id", "type": "webhook.test", "createdAt",
"data": {"endpointId", "message"}}). El parser igual es TOLERANTE —acepta también
los nombres del objeto de GET /messages (`counterpartPhone`, `body`, `id`) y otras
envolturas— por si Conecta cambia el formato. `forma_del_payload()` devuelve solo
las CLAVES (sin valores) para registrarlas en el log.

NÚMEROS EXTRANJEROS. POST /messages solo acepta números de Ecuador (09… / +5939…):
a un +51 responde 400 INVALID_PHONE. El agente responde igual, pero el envío falla.

Qué se descarta (devuelve None): eventos que no son `message.received`, mensajes
salientes (`direction` != INBOUND), mensajes de otra conexión, y mensajes sin número
o sin nada que responder.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass

from . import config

EVENTO_MENSAJE = "message.received"
EVENTO_PRUEBA = "webhook.test"

_TIPOS_AUDIO = {"audio", "ptt", "voice", "audiomessage"}


@dataclass
class MensajeEntrante:
    telefono: str
    texto: str
    id_mensaje: str | None = None
    nombre_perfil: str | None = None
    es_audio: bool = False
    media_file_id: str | None = None
    # True si el mensaje trae coordenadas (ubicación de WhatsApp). MAD las usa para
    # activar la asistencia a eventos; el canal las extrae del payload completo.
    tiene_ubicacion: bool = False
    contact_id: str | None = None


def firma_valida(cuerpo: bytes, cabecera: str | None, ahora: float | None = None) -> bool:
    """True si la firma corresponde al secreto.

    Sin secreto configurado devuelve False (fail-closed), salvo que
    SMARTGOB_ALLOW_UNSIGNED esté activado para desarrollo.
    """
    if not config.SMARTGOB_WEBHOOK_SECRET:
        return config.SMARTGOB_ALLOW_UNSIGNED
    if not cabecera:
        return False
    try:
        partes = dict(p.strip().split("=", 1) for p in cabecera.split(",") if "=" in p)
        t, v1 = partes.get("t", ""), partes.get("v1", "")
        # isascii: "²" pasa isdigit() pero int() lo rechaza; un v1 no ASCII hace fallar
        # compare_digest. Los dos deben dar 401, no 500.
        if not (t.isascii() and t.isdigit() and v1 and v1.isascii()):
            return False
        ahora = time.time() if ahora is None else ahora
        if abs(ahora - int(t)) > config.TOLERANCIA_FIRMA_SEGUNDOS:
            return False
        esperado = hmac.new(config.SMARTGOB_WEBHOOK_SECRET.encode(),
                            t.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, v1)
    except (TypeError, ValueError):
        return False


def tipo_de_evento(payload: dict, cabecera: str | None) -> str:
    """El tipo viene en `X-Conecta-Event-Type`; si no, en el cuerpo."""
    if cabecera:
        return cabecera.strip().lower()
    if isinstance(payload, dict):
        for clave in ("event", "type", "eventType"):
            if isinstance(payload.get(clave), str):
                return payload[clave].strip().lower()
    return ""


def forma_del_payload(x, profundidad: int = 0):
    """Estructura del payload SIN valores (para el log): {"data": {"body": "str", ...}}."""
    if isinstance(x, dict) and profundidad < 4:
        return {k: forma_del_payload(v, profundidad + 1) for k, v in x.items()}
    if isinstance(x, list):
        return [forma_del_payload(x[0], profundidad + 1)] if x else []
    return type(x).__name__


def _primero(d: dict, *claves):
    for clave in claves:
        valor = d.get(clave)
        if valor not in (None, ""):
            return valor
    return None


def _mensaje(payload: dict) -> dict | None:
    data = payload.get("data")
    for candidato in (
        data.get("message") if isinstance(data, dict) else None,
        data,
        payload.get("message"),
        payload,
    ):
        if isinstance(candidato, dict) and _primero(candidato, "body", "text", "from",
                                                    "counterpartPhone", "messageType"):
            return candidato
    return None


_CLAVES_LATITUD = {"lat", "latitude", "latitud"}


def _tiene_coordenadas(nodo, profundidad: int = 0) -> bool:
    """True si algún objeto del payload tiene una clave de latitud (ubicación)."""
    if profundidad > 8:
        return False
    if isinstance(nodo, dict):
        return any(str(k).strip().lower() in _CLAVES_LATITUD for k in nodo) or any(
            _tiene_coordenadas(v, profundidad + 1) for v in nodo.values())
    if isinstance(nodo, list):
        return any(_tiene_coordenadas(x, profundidad + 1) for x in nodo[:20])
    return False


# JIDs de WhatsApp que NO son una persona: grupos, LID (id opaco), estados, canales.
_SUFIJOS_NO_PERSONA = ("@g.us", "@lid", "@broadcast", "@newsletter")


def _solo_digitos(numero) -> str:
    """Teléfono como dígitos con código de país, o "" si no es el número de una persona.

    Un JID de grupo o un LID no es un teléfono: tomarlo como tal crearía un hilo
    compartido por todo un grupo, o guardaría un "+<lid>" de 18 dígitos como teléfono
    del ciudadano. Se exige la longitud de E.164 (8 a 15 dígitos).
    """
    crudo = str(numero or "").strip().lower()
    if crudo.endswith(_SUFIJOS_NO_PERSONA):
        return ""
    digitos = "".join(c for c in crudo.split("@", 1)[0] if c.isascii() and c.isdigit())
    # 09XXXXXXXX (formato local de Ecuador) -> 5939XXXXXXXX, para un solo thread por persona.
    if len(digitos) == 10 and digitos.startswith("09"):
        digitos = "593" + digitos[1:]
    return digitos if 8 <= len(digitos) <= 15 else ""


def parsear(payload: dict, tipo: str) -> MensajeEntrante | None:
    """Del JSON del webhook a un `MensajeEntrante`, o None si no hay que responder."""
    if tipo != EVENTO_MENSAJE or not isinstance(payload, dict):
        return None
    msg = _mensaje(payload)
    if msg is None:
        return None

    direccion = str(msg.get("direction") or "INBOUND").upper()
    if direccion != "INBOUND":
        return None
    # FAIL-CLOSED: la credencial y el webhook son por ORGANIZACIÓN de Conecta, que puede
    # tener varias conexiones (números). Solo se atiende la de este agente: sin
    # SMARTGOB_CONNECTION_ID, o con un mensaje sin connectionId, se descarta. Si no,
    # el WhatsApp de otra campaña de la misma organización entraría a este agente.
    conexion = _primero(msg, "connectionId") or (msg.get("connection") or {}).get("id")
    if not config.SMARTGOB_CONNECTION_ID or conexion != config.SMARTGOB_CONNECTION_ID:
        return None

    telefono = _solo_digitos(_primero(msg, "from", "counterpartPhone", "phone")
                             or _primero(payload, "from"))
    if not telefono:
        return None

    texto = _primero(msg, "body", "text", "content")
    if texto is None and isinstance(msg.get("message"), str):
        texto = msg["message"]
    texto = str(texto or "").strip()
    tipo_msg = str(msg.get("messageType") or msg.get("type") or "").lower()
    mimetype = str(msg.get("mimetype") or (msg.get("mediaFile") or {}).get("mimeType") or "")
    es_audio = not texto and (tipo_msg in _TIPOS_AUDIO or mimetype.startswith("audio/")
                              or (bool(msg.get("hasMedia")) and "audio" in tipo_msg))
    tiene_ubicacion = _tiene_coordenadas(msg)
    if not texto and not es_audio and not tiene_ubicacion:
        return None   # imagen sin pie, sticker...: nada que responder

    contacto = msg.get("contact") if isinstance(msg.get("contact"), dict) else {}
    nombre = _primero(contacto, "fullName", "name") or _primero(msg, "pushName", "profileName")

    return MensajeEntrante(
        telefono=telefono,
        texto=texto,
        id_mensaje=_primero(msg, "id", "messageId"),
        nombre_perfil=str(nombre).strip() if nombre else None,
        es_audio=es_audio,
        media_file_id=_primero(msg, "mediaFileId"),
        contact_id=_primero(msg, "contactId"),
        tiene_ubicacion=tiene_ubicacion,
    )
