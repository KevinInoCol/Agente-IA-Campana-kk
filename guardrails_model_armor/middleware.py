"""
Middleware que acopla Model Armor al agente (hook `before_agent`).
==================================================================

Mismo patrón que `input_guardrails/guardrail_middleware.py` (LangChain v1, "before
agent guardrails"): revisa el último mensaje humano ANTES de que el agente haga nada;
si Model Armor lo bloquea, corta la corrida (`jump_to="end"`) y responde con el texto
de `respuesta_bloqueada`, sin llamar al LLM ni a las tools.

Va DESPUÉS del guardrail local en la lista de middleware: lo que las capas locales ya
bloquearon (gratis e instantáneo) no llega a gastar una llamada a Google Cloud.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langgraph.runtime import Runtime

from input_guardrails.guardrail_middleware import _texto_del_mensaje
from input_guardrails.input_guardrail import respuesta_bloqueada

from .cliente import revisar


class ModelArmorMiddleware(AgentMiddleware):
    """Revisa la query entrante con Model Armor (Google Cloud)."""

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        mensajes = state.get("messages") or []
        if not mensajes:
            return None
        ultimo = mensajes[-1]
        if getattr(ultimo, "type", None) != "human":
            return None

        veredicto = revisar(_texto_del_mensaje(getattr(ultimo, "content", "")))
        if not veredicto.bloquear:
            return None
        return {
            "messages": [{"role": "assistant", "content": respuesta_bloqueada(veredicto.motivo)}],
            "jump_to": "end",
        }


def construir_middleware_model_armor() -> ModelArmorMiddleware:
    """Middleware a pasar a `create_agent(middleware=...)`."""
    return ModelArmorMiddleware()
