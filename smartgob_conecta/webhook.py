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


def firma_valida(cuerpo: bytes, cabecera: str | None, ahora: float | None = None) -> bool:
    """True si la firma corresponde al secreto.

    Sin secreto configurado devuelve False (fail-closed), salvo que
    SMARTGOB_ALLOW_UNSIGNED esté activado para desarrollo.
    """
    if not config.SMARTGOB_WEBHOOK_SECRET:
        return config.SMARTGOB_ALLOW_UNSIGNED
    if not cabecera:
        return False
    partes = dict(p.strip().split("=", 1) for p in cabecera.split(",") if "=" in p)
    t, v1 = partes.get("t", ""), partes.get("v1", "")
    if not t.isdigit() or not v1:
        return False
    ahora = time.time() if ahora is None else ahora
    if abs(ahora - int(t)) > config.TOLERANCIA_FIRMA_SEGUNDOS:
        return False
    esperado = hmac.new(config.SMARTGOB_WEBHOOK_SECRET.encode(),
                        t.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, v1)


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


def _solo_digitos(numero) -> str:
    texto = str(numero or "").split("@", 1)[0]
    digitos = "".join(c for c in texto if c.isdigit())
    # 09XXXXXXXX (formato local de Ecuador) -> 5939XXXXXXXX, para un solo thread por persona.
    if len(digitos) == 10 and digitos.startswith("09"):
        digitos = "593" + digitos[1:]
    return digitos


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
    conexion = _primero(msg, "connectionId") or (msg.get("connection") or {}).get("id")
    if config.SMARTGOB_CONNECTION_ID and conexion and conexion != config.SMARTGOB_CONNECTION_ID:
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
    if not texto and not es_audio:
        return None   # imagen sin pie, sticker, ubicación...: nada que responder

    contacto = msg.get("contact") if isinstance(msg.get("contact"), dict) else {}
    nombre = _primero(contacto, "fullName", "name") or _primero(msg, "pushName", "profileName")

    return MensajeEntrante(
        telefono=telefono,
        texto=texto,
        id_mensaje=_primero(msg, "id", "messageId"),
        nombre_perfil=str(nombre).strip() if nombre else None,
        es_audio=es_audio,
        media_file_id=_primero(msg, "mediaFileId"),
    )
