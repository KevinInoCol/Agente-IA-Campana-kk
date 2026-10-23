"""InputGuardrail del agente: pipeline LOCAL de seguridad de entrada (6 capas, sin red)."""

from .guardrail_middleware import construir_middleware_guardrail

__all__ = ["construir_middleware_guardrail"]
