"""
Middleware que acopla el InputGuardrail (8 capas) al agente
===========================================================

Envuelve el pipeline de seguridad de entrada (`input_guardrail.py`) como un
`AgentMiddleware` de LangChain v1 con hook `before_agent`: cada query del usuario
pasa por los filtros ANTES de que el agente procese nada. Si alguna capa bloquea,
el middleware corta el flujo (`jump_to="end"`) y devuelve la respuesta canned
correspondiente SIN llamar al LLM, a las tools ni al retrieval.

Patrón oficial "Before agent guardrails" de LangChain v1
(docs.langchain.com/oss/python/langchain/guardrails#before-agent-guardrails),
verificado contra `langchain 1.3.11`: un hook `before_agent` decorado con
`@hook_config(can_jump_to=["end"])` que retorna
`{"messages": [...], "jump_to": "end"}` para terminar la corrida temprano.

Orden respecto al PIIMiddleware: este guardrail va PRIMERO en la lista de
middleware, así su `before_agent` corre antes del `before_model` de la redacción
de PII y ve el input CRUDO (necesario para detectar inyección/toxicidad/secretos
sobre el texto original).

Nota de memoria/observabilidad: al ser middleware, el turno bloqueado (query +
respuesta canned) SÍ queda en el checkpointer de Postgres y en la traza de
Langfuse (útil para auditar intentos), aunque no consume el LLM.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langgraph.runtime import Runtime

from .input_guardrail import respuesta_bloqueada, verificar_input_guardrail


def _texto_del_mensaje(contenido: Any) -> str:
    """Normaliza el `content` de un mensaje a texto plano.

    El `content` puede venir como string o como lista de bloques (según el
    canal/modelo); el guardrail espera un string.
    """
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        partes = []
        for bloque in contenido:
            if isinstance(bloque, str):
                partes.append(bloque)
            elif isinstance(bloque, dict):
                partes.append(str(bloque.get("text", "")))
        return " ".join(p for p in partes if p)
    return str(contenido) if contenido is not None else ""


class InputGuardrailMiddleware(AgentMiddleware):
    """Guardrail de entrada (8 capas) como middleware `before_agent` del agente."""

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Valida la última query humana; si se bloquea, corta y responde canned."""
        mensajes = state.get("messages") or []
        if not mensajes:
            return None

        # La query entrante es el último mensaje del estado (con memoria, el
        # historial va antes y el mensaje nuevo queda al final).
        ultimo = mensajes[-1]
        if getattr(ultimo, "type", None) != "human":
            return None

        texto = _texto_del_mensaje(getattr(ultimo, "content", ""))
        es_seguro, motivo = verificar_input_guardrail(texto)
        if es_seguro:
            return None

        # Bloqueado: terminar la corrida con la respuesta canned del motivo.
        return {
            "messages": [{"role": "assistant", "content": respuesta_bloqueada(motivo)}],
            "jump_to": "end",
        }


def construir_middleware_guardrail() -> InputGuardrailMiddleware:
    """Devuelve el middleware de guardrail a pasar a `create_agent(middleware=...)`."""
    return InputGuardrailMiddleware()
