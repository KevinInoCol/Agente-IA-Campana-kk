"""
Middleware: le dice al modelo qué datos del registro ya tiene la persona.
=========================================================================

El cliente pidió que el bot pida SOLO los datos que faltan ("ya lo tendrían en bd").
El modelo no puede saberlo por sí mismo: la memoria de la conversación no sabe qué hay
en la base, y una persona registrada ayer puede volver hoy. Por eso, ANTES de cada
llamada al modelo se consulta `ciudadanos` por el número de WhatsApp (que pone el
canal en el contexto) y se agrega al system prompt un bloque `<estado_del_registro>`.

Se consulta en CADA llamada al modelo, no una vez por mensaje: si en el mismo turno
la tool `registrar_ciudadano` acaba de guardar, la siguiente llamada ya lo ve.

La cédula NO se pasa al modelo: solo si está registrada o no. El modelo no la
necesita para nada y así no vuelve a aparecer en las respuestas.

Patrón oficial de LangChain v1 (verificado con el MCP docs-langchain, 2026-10-22):
`AgentMiddleware.wrap_model_call` + `request.override(system_message=...)`, agregando
un bloque a `request.system_message.content_blocks`.
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage

from simpatizantes import repositorio

logger = logging.getLogger(__name__)


def describir_estado(registro: dict | None, consultado: bool = True) -> str:
    """Texto del bloque <estado_del_registro> (función pura, testeable)."""
    if not consultado:
        estado = ("No se pudo consultar el registro en este momento. Trátala como no "
                  "registrada, pero no insistas si dice que ya se registró.")
    elif not registro:
        estado = ("La persona NO está registrada. Faltan: cédula, nombres completos, "
                  "barrio y correo (opcional).")
    else:
        tiene = []
        faltan = []
        if registro.get("cedula"):
            tiene.append("cédula registrada (el número no se muestra)")
        else:
            faltan.append("cédula")
        for clave, nombre in (("nombres", "nombres"), ("barrio", "barrio")):
            if registro.get(clave):
                tiene.append(f"{nombre}: {registro[clave]}")
            else:
                faltan.append(nombre)
        if registro.get("correo"):
            tiene.append("correo registrado")
        else:
            faltan.append("correo (opcional)")
        completo = all(registro.get(k) for k in ("cedula", "nombres", "barrio"))
        estado = ("La persona YA está registrada. " if completo
                  else "Registro INCOMPLETO. ") + "Datos guardados: " + "; ".join(tiene) + "."
        if faltan:
            estado += " Faltan: " + ", ".join(faltan) + "."
    return f"<estado_del_registro>\n{estado}\n</estado_del_registro>"


class EstadoRegistroMiddleware(AgentMiddleware):
    """Agrega <estado_del_registro> al system prompt antes de cada llamada al modelo."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        contexto = getattr(request.runtime, "context", None)
        telefono = getattr(contexto, "telefono", None)
        if not telefono or not repositorio.esta_configurado():
            # Sin número (CLI sin --telefono) o sin base: no hay registro que consultar.
            texto = describir_estado(None)
        else:
            try:
                texto = describir_estado(repositorio.buscar_ciudadano(telefono))
            except Exception:  # noqa: BLE001 — nunca dejar a la persona sin respuesta
                logger.exception("No se pudo consultar el registro del ciudadano")
                texto = describir_estado(None, consultado=False)

        bloques = list(request.system_message.content_blocks) + [{"type": "text", "text": texto}]
        return handler(request.override(system_message=SystemMessage(content=bloques)))
