"""
Middleware: le dice al modelo en qué paso del registro está la persona.
=======================================================================

El flujo v2 del cliente (Flujo_Agente_Juan_Jose.docx) es un recorrido con orden fijo:
cédula -> nombres -> correo -> teléfono -> barrio -> resumen -> menú. El
modelo no puede saber en qué paso está una persona: la memoria de la conversación no
sabe qué hay en la base, y alguien que dejó el registro a medias ayer puede volver
hoy. Por eso, ANTES de cada llamada al modelo se consulta la base por el número de
WhatsApp (que pone el canal en el contexto) y se agrega al system prompt un bloque
<estado_del_registro> con el paso pendiente, los datos guardados y la colaboración
en borrador.

Se consulta en CADA llamada al modelo, no una vez por mensaje: si en el mismo turno
una tool acaba de guardar, la siguiente llamada ya lo ve.

La cédula NO se pasa al modelo. Correo y teléfono de contacto van enmascarados: el
modelo solo los necesita para el resumen, que los muestra así.

Patrón oficial de LangChain v1 (verificado con el MCP docs-langchain, 2026-10-22):
`AgentMiddleware.wrap_model_call` + `request.override(system_message=...)`, agregando
un bloque a `request.system_message.content_blocks`.
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage

from dominio import registro as paso
from dominio.telefono import enmascarar_correo, enmascarar_telefono
from simpatizantes import repositorio

logger = logging.getLogger(__name__)

_QUE_PEDIR = {
    paso.CEDULA: "pedir el número de cédula (F03)",
    paso.CONFIRMAR_NOMBRES: "preguntar si los nombres recuperados son correctos (F04)",
    paso.CORREO: "pedir el correo electrónico (F05)",
    paso.TELEFONO: "pedir el teléfono de contacto (F05)",
    paso.BARRIO: "pedir el barrio o sector (F05)",
    paso.REVISAR_PERFIL: "mostrar el resumen de datos y pedir que los confirme (F06)",
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
        estado_nombres = "confirmados" if r.get("nombres_confirmados") else "SIN confirmar"
        lineas.append(f"nombre_completo: {paso.nombre_para_mostrar(r['nombres'])} "
                      f"({estado_nombres})")
        lineas.append(f"nombre: {paso.nombre_de_pila(r['nombres'])}")
    if r.get("correo"):
        lineas.append(f"correo_enmascarado: {enmascarar_correo(r['correo'])}")
    if r.get("telefono_contacto"):
        lineas.append(f"telefono_enmascarado: {enmascarar_telefono(r['telefono_contacto'])}")
    if r.get("barrio"):
        lineas.append(f"barrio: {r['barrio']}")

    if borrador:
        lineas.append(f"colaboracion_en_borrador: «{borrador['ayuda']}» (preparada, SIN "
                      "confirmar: ofrece retomarla mostrando su resumen)")
    return "<estado_del_registro>\n" + "\n".join(lineas) + "\n</estado_del_registro>"


def _consultar(telefono: str | None) -> str:
    if not telefono or not repositorio.esta_configurado():
        # Sin número (CLI sin --telefono) o sin base: no hay registro que consultar.
        return describir_estado(None)
    try:
        registro = repositorio.buscar_ciudadano(telefono)
        borrador = repositorio.obtener_borrador(telefono) if registro else None
        return describir_estado(registro, borrador)
    except Exception:  # noqa: BLE001 — nunca dejar a la persona sin respuesta
        logger.exception("No se pudo consultar el registro del ciudadano")
        return describir_estado(None, consultado=False)


class EstadoRegistroMiddleware(AgentMiddleware):
    """Agrega <estado_del_registro> al system prompt antes de cada llamada al modelo."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        contexto = getattr(request.runtime, "context", None)
        texto = _consultar(getattr(contexto, "telefono", None))
        bloques = list(request.system_message.content_blocks) + [{"type": "text", "text": texto}]
        return handler(request.override(system_message=SystemMessage(content=bloques)))
