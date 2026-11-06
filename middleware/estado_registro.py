"""
Middleware: le dice al modelo en qué paso del registro está la persona.
=======================================================================

El flujo v4 del cliente (TOTOBOT.docx) es un recorrido con orden fijo:
cédula -> correo -> celular -> barrio -> menú. El
modelo no puede saber en qué paso está una persona: la memoria de la conversación no
sabe qué hay en la base, y alguien que dejó el registro a medias ayer puede volver
hoy. Por eso, ANTES de cada llamada al modelo se consulta la base por el número de
WhatsApp (que pone el canal en el contexto) y se agrega al system prompt un bloque
<estado_del_registro> con el paso pendiente, los datos guardados y la colaboración
en borrador.

Se consulta en CADA llamada al modelo, no una vez por mensaje: si en el mismo turno
una tool acaba de guardar, la siguiente llamada ya lo ve.

La cédula NO se pasa al modelo. Correo y teléfono de contacto van enmascarados: el
modelo no necesita verlos completos.

Patrón oficial de LangChain v1 (verificado con el MCP docs-langchain, 2026-10-22):
`AgentMiddleware.wrap_model_call` + `request.override(system_message=...)`, agregando
un bloque a `request.system_message.content_blocks`.

AVISO DE PROTECCIÓN DE DATOS (LOPDP) — GARANTIZADO POR CÓDIGO
Nadie entrega su cédula sin haber visto antes el aviso de la BIENVENIDA ("al
ingresar los datos, aceptas..."). No se deja a criterio del modelo: si la persona
está en el paso CEDULA y ningún mensaje del asistente en su conversación contiene el
aviso, cualquier respuesta del modelo (texto o llamada a una tool) se reemplaza por la
BIENVENIDA completa. Así tampoco se consulta la DINARDAP antes del aviso.
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import AIMessage, SystemMessage

from dominio import registro as paso
from dominio.telefono import enmascarar_correo, enmascarar_telefono
from simpatizantes import repositorio

logger = logging.getLogger(__name__)

# Debe coincidir con BIENVENIDA de prompt/prompt.yaml (lo verifica un test).
AVISO_DATOS = ("al ingresar los datos, aceptas que usemos tu información para "
               "contactarte sobre nuestras actividades")
BIENVENIDA = (
    "¡Qué fue! 👋 Soy TotoBot, encantado de saludarte. Para ayudarte de manera "
    "personalizada, necesito unos datos rápidos; " + AVISO_DATOS + ". 🔒\n\n"
    "Empecemos: ¿cuál es tu número de cédula? 🪪"
)

_QUE_PEDIR = {
    paso.CEDULA: "pedir el número de cédula",
    paso.CORREO: "pedir el correo electrónico",
    paso.TELEFONO: "pedir el número de celular",
    paso.BARRIO: "pedir el barrio o sector",
    paso.COMPLETO: "nada: el registro está completo y el menú está habilitado",
}


def describir_estado(registro: dict | None, borrador: dict | None = None,
                     consultado: bool = True) -> str:
    """Texto del bloque <estado_del_registro> (función pura, testeable)."""
    if not consultado:
        estado = ("No se pudo consultar el registro en este momento. No pidas datos ni "
                  "abras el menú: dile que hay un problema temporal y que lo intente en "
                  "unos minutos.")
        return f"<estado_del_registro>\n{estado}\n</estado_del_registro>"

    r = registro or {}
    siguiente = paso.siguiente_paso(r)
    lineas = [f"paso_pendiente: {siguiente} -> {_QUE_PEDIR[siguiente]}"]

    if r.get("cedula") and not r.get("nombres"):
        lineas.append("cedula: guardada de un registro anterior, sin consultar en el registro "
                      "civil (llama a consultar_cedula con cedula vacía)")
    elif r.get("cedula"):
        lineas.append("cedula: guardada (el número no se muestra)")
    if r.get("nombres"):
        lineas.append(f"nombre_completo: {paso.nombre_para_mostrar(r['nombres'])}")
        lineas.append(f"nombre: {paso.nombre_de_pila(r['nombres'])}")
    if r.get("correo"):
        lineas.append(f"correo_enmascarado: {enmascarar_correo(r['correo'])}")
    if r.get("telefono_contacto"):
        lineas.append(f"telefono_enmascarado: {enmascarar_telefono(r['telefono_contacto'])}")
    if r.get("barrio"):
        lineas.append(f"barrio: {r['barrio']}")

    if borrador:
        lineas.append(f"colaboracion_en_borrador: «{borrador['ayuda']}» (preparada, SIN "
                      "enviar: ofrece retomarla mostrando su texto)")
    return "<estado_del_registro>\n" + "\n".join(lineas) + "\n</estado_del_registro>"


def _texto_de(mensaje) -> str:
    try:
        return mensaje.text or ""
    except Exception:  # noqa: BLE001
        return str(getattr(mensaje, "content", "") or "")


def aviso_mostrado(mensajes) -> bool:
    """True si algún mensaje del asistente en la conversación ya trae el aviso."""
    return any(getattr(m, "type", "") == "ai" and AVISO_DATOS in _texto_de(m)
               for m in mensajes or [])


def _consultar(telefono: str | None) -> tuple[str, str | None]:
    """(<estado_del_registro>, paso pendiente o None si no se pudo consultar)."""
    if not telefono or not repositorio.esta_configurado():
        # Sin número (CLI sin --telefono) o sin base: no hay registro que consultar.
        return describir_estado(None), paso.CEDULA
    try:
        registro = repositorio.buscar_ciudadano(telefono)
        borrador = repositorio.obtener_borrador(telefono) if registro else None
        return describir_estado(registro, borrador), paso.siguiente_paso(registro or {})
    except Exception:  # noqa: BLE001 — nunca dejar a la persona sin respuesta
        logger.exception("No se pudo consultar el registro del ciudadano")
        return describir_estado(None, consultado=False), None


class EstadoRegistroMiddleware(AgentMiddleware):
    """Agrega <estado_del_registro> al system prompt antes de cada llamada al modelo
    y garantiza el aviso de protección de datos antes de pedir/procesar la cédula."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        contexto = getattr(request.runtime, "context", None)
        texto, pendiente = _consultar(getattr(contexto, "telefono", None))
        falta_aviso = pendiente == paso.CEDULA and not aviso_mostrado(request.messages)
        if falta_aviso:
            texto = texto.replace(
                "\n</estado_del_registro>",
                "\naviso_de_datos: NO mostrado en esta conversación -> responde con "
                "BIENVENIDA completa, aunque la persona ya haya escrito antes o haya "
                "enviado su cédula. No llames a consultar_cedula todavía."
                "\n</estado_del_registro>")
        bloques = list(request.system_message.content_blocks) + [{"type": "text", "text": texto}]
        respuesta = handler(request.override(system_message=SystemMessage(content=bloques)))
        if falta_aviso:
            respuesta = _forzar_bienvenida(respuesta)
        return respuesta


def _forzar_bienvenida(respuesta: ModelResponse) -> ModelResponse:
    """Si el modelo no envió el aviso, su respuesta se reemplaza por la BIENVENIDA."""
    ai = next((m for m in respuesta.result if getattr(m, "type", "") == "ai"), None)
    if ai is not None and AVISO_DATOS in _texto_de(ai) and not getattr(ai, "tool_calls", None):
        return respuesta
    logger.info("registro: aviso de datos forzado (el modelo no lo envió)")
    return ModelResponse(result=[AIMessage(content=BIENVENIDA, id=getattr(ai, "id", None))])
