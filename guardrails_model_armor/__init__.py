"""
Guardrail de entrada con Model Armor (Google Cloud).
====================================================

Reemplaza a las antiguas capas 7 y 8 (modelos de IA en Groq) de `input_guardrails/`.
Las capas locales 1-6 siguen allí y corren antes que esta.

⚠️ ESTADO (2026-10-23): programado pero APAGADO. Falta la cuenta de GCP. Para
activarlo, seguir `guardrails_model_armor/README.md` (los pasos terminan
descomentando dos líneas en `agent.py`).
"""

from .cliente import Veredicto, es_respuesta_trivial, revisar
from .middleware import ModelArmorMiddleware, construir_middleware_model_armor

__all__ = [
    "ModelArmorMiddleware",
    "Veredicto",
    "construir_middleware_model_armor",
    "es_respuesta_trivial",
    "revisar",
]
