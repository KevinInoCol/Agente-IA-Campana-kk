"""
Canal WhatsApp vía SmartGob Conecta (FastAPI).
==============================================

Recibe el webhook `message.received` de SmartGob Conecta, junta los mensajes en
ráfaga (debounce en Redis), invoca al agente y responde con POST /messages.

Flujo por mensaje:
    webhook -> firma HMAC -> parsear -> deduplicar -> (audio -> Whisper) -> debounce
            -> agente (threadpool) -> Conecta POST /messages

El webhook responde 200 de inmediato y el trabajo sigue en segundo plano: Conecta
exige respuesta en menos de 10 s y reintenta hasta 8 veces si no llega, mientras que
un turno con debounce de 15 s más el LLM tarda bastante más. Responder tarde
provocaría reintentos y respuestas duplicadas.

Uso local:
    uvicorn main_conecta:app --reload --port 8000

Configuración en SmartGob Conecta (Administración -> Integraciones -> Webhooks,
requiere administrador con segundo factor):
    URL de destino: https://<tu-dominio>/webhook/conecta   (solo https público)
    Eventos:        message.received
    El panel muestra el "Secreto de firma del webhook" UNA sola vez:
    va en SMARTGOB_WEBHOOK_SECRET.
"""

import asyncio
import json
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

import smartgob_conecta
from agent import TENANT, TOOLS_CABLEADAS, construir_agente, flush_langfuse, traza
from agent_context import ConversacionContext
from conversation_history import close_checkpointer
from debounce import (
    BUFFER_ENABLED,
    BUFFER_WINDOW_SECONDS,
    KEY_PREFIX_GENERICO,
    REDIS_KEY_PREFIX,
    close_redis,
    encolar_mensaje,
    get_redis,
)
from simpatizantes import apps_script, repositorio
from smartgob_conecta import config as conecta_config
from transcripcion import transcribir_audio

APP_VERSION = os.getenv("APP_VERSION", "dev")

# Cuánto se recuerda un evento ya procesado (Conecta reintenta con espera exponencial).
DEDUP_TTL_SECONDS = 24 * 3600

# Cuántos payloads se registran con su ESTRUCTURA (claves, sin valores) al arrancar.
# El JSON de `message.received` no está documentado: así se confirma con los primeros
# mensajes reales sin dejar datos personales en el log.
PAYLOADS_A_REGISTRAR = 5

MENSAJE_AUDIO_FALLIDO = "No pude escuchar tu audio. ¿Me lo escribes, por favor?"
MENSAJE_ERROR = ("Gracias por tu mensaje. Tuve un problema para responderte; "
                 "¿me escribes de nuevo en un momento?")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("canal_conecta")

_agente = None
_payloads_registrados = 0

# Nombre de perfil y contactId por teléfono. El debounce solo transporta
# (conversación, texto), así que se recuerdan aquí hasta que se procesa.
_nombres_perfil: dict[str, str] = {}
_contact_ids: dict[str, str] = {}

# Respaldo de deduplicación en memoria cuando no hay Redis.
_vistos_local: "OrderedDict[str, None]" = OrderedDict()

_tareas: "set[asyncio.Task]" = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agente
    _agente = construir_agente()

    if not conecta_config.esta_configurado():
        logger.warning("⚠️  SmartGob Conecta sin configurar (SMARTGOB_API_KEY / "
                       "SMARTGOB_CONNECTION_ID): el agente responderá pero los mensajes NO "
                       "se enviarán.")
    if not conecta_config.SMARTGOB_WEBHOOK_SECRET:
        if conecta_config.SMARTGOB_ALLOW_UNSIGNED:
            logger.warning("⚠️  SMARTGOB_WEBHOOK_SECRET vacío y SMARTGOB_ALLOW_UNSIGNED=true: "
                           "se aceptan webhooks SIN firma. Solo para desarrollo.")
        else:
            logger.warning("⚠️  SMARTGOB_WEBHOOK_SECRET vacío: el webhook RECHAZA todo (401) "
                           "hasta configurar el secreto de firma.")
    if not repositorio.esta_configurado() and not apps_script.esta_configurado():
        logger.warning("⚠️  Ni base de datos ni APPS_SCRIPT_WEBHOOK_URL: 'Quiero sumarme' no "
                       "podrá guardar a nadie.")
    if BUFFER_ENABLED and KEY_PREFIX_GENERICO:
        logger.warning("⚠️  Debounce activo sin DB_SCHEMA ni DEBOUNCE_KEY_PREFIX: las claves de "
                       "Redis usan el prefijo genérico '%s'.", REDIS_KEY_PREFIX)
    logger.info("Canal SmartGob Conecta listo · versión %s · debounce=%s (%ss)",
                APP_VERSION, BUFFER_ENABLED, BUFFER_WINDOW_SECONDS)
    yield
    await close_redis()
    close_checkpointer()
    repositorio.cerrar()
    flush_langfuse()


app = FastAPI(
    title="Agente WhatsApp · Toto Reyes",
    description="Asistente de la campaña de Juan José Reyes Baquerizo (SmartGob Conecta).",
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.get("/", tags=["Agente"])
def raiz() -> dict:
    return {"servicio": "agente-toto-reyes", "version": APP_VERSION,
            "webhook": "/webhook/conecta"}


@app.get("/health", tags=["Agente"])
def health() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "tenant": TENANT or None,
        "tools": [t.name for t in TOOLS_CABLEADAS],
        "conecta": {"configurado": conecta_config.esta_configurado(),
                    "conexion": conecta_config.SMARTGOB_CONNECTION_ID or None,
                    "firma_verificada": bool(conecta_config.SMARTGOB_WEBHOOK_SECRET)},
        "debounce": {"activo": BUFFER_ENABLED, "ventana_s": BUFFER_WINDOW_SECONDS,
                     "namespace": REDIS_KEY_PREFIX},
        "simpatizantes": {"postgres": repositorio.esta_configurado(),
                          "apps_script": apps_script.esta_configurado()},
    }


def _thread_id(telefono: str) -> str:
    return f"whatsapp:{telefono}"


def _invocar_agente(texto: str, telefono: str, nombre_perfil: str | None) -> str:
    """Invoca al agente (bloqueante; se corre en el threadpool)."""
    thread_id = _thread_id(telefono)
    with traza(session_id=thread_id):
        resultado = _agente.invoke(
            {"messages": [{"role": "user", "content": texto}]},
            {"configurable": {"thread_id": thread_id}},
            context=ConversacionContext(telefono=telefono, nombre_perfil=nombre_perfil,
                                        canal="whatsapp"),
        )
    return (resultado["messages"][-1].content or "").strip()


async def _procesar(telefono: str, texto: str) -> None:
    """Responde a `texto` (ya concatenado por el debounce) y envía la respuesta."""
    nombre_perfil = _nombres_perfil.pop(telefono, None)
    contact_id = _contact_ids.pop(telefono, None)
    try:
        respuesta = await run_in_threadpool(_invocar_agente, texto, telefono, nombre_perfil)
    except Exception:  # noqa: BLE001 — la persona no puede quedarse sin respuesta
        logger.exception("Falló la invocación del agente")
        respuesta = MENSAJE_ERROR
    if respuesta:
        await smartgob_conecta.enviar_texto(telefono, respuesta, contact_id)


async def _ya_procesado(clave_evento: str | None) -> bool:
    """True si este evento ya llegó antes (reintento de Conecta)."""
    if not clave_evento:
        return False
    clave = f"{REDIS_KEY_PREFIX}:conecta:visto:{clave_evento}"
    if BUFFER_ENABLED:
        try:
            nuevo = await get_redis().set(clave, "1", nx=True, ex=DEDUP_TTL_SECONDS)
            return not nuevo
        except Exception:  # noqa: BLE001 — si Redis falla, seguimos con la memoria local
            logger.warning("Redis no disponible para deduplicar; uso memoria local.")
    if clave_evento in _vistos_local:
        return True
    _vistos_local[clave_evento] = None
    if len(_vistos_local) > 5000:
        _vistos_local.popitem(last=False)
    return False


async def _atender(mensaje: "smartgob_conecta.MensajeEntrante") -> None:
    """Todo lo que pasa después de devolverle 200 a Conecta."""
    texto = mensaje.texto
    if mensaje.es_audio:
        try:
            contenido, mimetype = await smartgob_conecta.descargar_media(
                mensaje.media_file_id, mensaje.id_mensaje)
            texto = await run_in_threadpool(transcribir_audio, contenido, mimetype)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo transcribir el audio")
            texto = ""
        if not texto:
            await smartgob_conecta.enviar_texto(mensaje.telefono, MENSAJE_AUDIO_FALLIDO,
                                                mensaje.contact_id)
            return

    if mensaje.nombre_perfil:
        _nombres_perfil[mensaje.telefono] = mensaje.nombre_perfil
    if mensaje.contact_id:
        _contact_ids[mensaje.telefono] = mensaje.contact_id

    if BUFFER_ENABLED:
        try:
            await encolar_mensaje(mensaje.telefono, texto, _procesar)
            return
        except Exception:  # noqa: BLE001 — sin Redis, se responde mensaje a mensaje
            logger.exception("Debounce no disponible; respondo sin concatenar")
    await _procesar(mensaje.telefono, texto)


@app.post("/webhook/conecta", tags=["Agente"])
async def webhook_conecta(
    request: Request,
    x_conecta_signature: str | None = Header(default=None),
    x_conecta_event_id: str | None = Header(default=None),
    x_conecta_event_type: str | None = Header(default=None),
) -> dict:
    """Webhook de SmartGob Conecta. La firma se verifica sobre el cuerpo CRUDO."""
    global _payloads_registrados
    cuerpo = await request.body()
    if not smartgob_conecta.firma_valida(cuerpo, x_conecta_signature):
        raise HTTPException(status_code=401, detail="Firma inválida")
    try:
        payload = json.loads(cuerpo)
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido") from None

    tipo = smartgob_conecta.tipo_de_evento(payload, x_conecta_event_type)
    if _payloads_registrados < PAYLOADS_A_REGISTRAR:
        _payloads_registrados += 1
        logger.info("Webhook %s · estructura: %s", tipo,
                    json.dumps(smartgob_conecta.forma_del_payload(payload), ensure_ascii=False))
    if tipo == smartgob_conecta.EVENTO_PRUEBA:
        logger.info("Evento de prueba de Conecta recibido y verificado.")
        return {"ok": True, "procesado": False, "motivo": "prueba"}

    mensaje = smartgob_conecta.parsear(payload, tipo)
    if mensaje is None:
        return {"ok": True, "procesado": False}
    if await _ya_procesado(x_conecta_event_id or mensaje.id_mensaje):
        return {"ok": True, "procesado": False, "motivo": "duplicado"}

    tarea = asyncio.create_task(_atender(mensaje))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return {"ok": True, "procesado": True}
