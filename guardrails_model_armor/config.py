"""
Configuración de Model Armor (Google Cloud).
============================================

Único lugar que lee las variables `MODEL_ARMOR_*`. Carga su propio `.env` porque sus
valores se leen a nivel de módulo (mismo patrón que `debounce/config.py`).

Model Armor revisa cada mensaje contra una PLANTILLA (template) creada en el proyecto
de GCP, con los filtros configurados allí (no en el código): inyección / jailbreak,
IA responsable (odio, acoso, sexual, peligroso), URLs maliciosas y CSAM. Ver
`guardrails_model_armor/README.md` para crearla.

Credenciales (en este orden):
  1. `MODEL_ARMOR_CREDENTIALS_JSON`: el CONTENIDO del JSON de la cuenta de servicio,
     pegado como variable de entorno. Es lo práctico en EasyPanel, donde no hay un
     archivo de credenciales montado.
  2. Si no está, Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS`
     apuntando a un archivo, o `gcloud auth application-default login` en local).
La cuenta necesita el rol `roles/modelarmor.user` en el proyecto de la plantilla.
"""

import os

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

MODEL_ARMOR_PROJECT_ID = os.getenv("MODEL_ARMOR_PROJECT_ID", "").strip()
# Región de la plantilla. Model Armor exige el endpoint REGIONAL para sanitizar.
MODEL_ARMOR_LOCATION = os.getenv("MODEL_ARMOR_LOCATION", "us-central1").strip()
MODEL_ARMOR_TEMPLATE_ID = os.getenv("MODEL_ARMOR_TEMPLATE_ID", "").strip()
MODEL_ARMOR_CREDENTIALS_JSON = os.getenv("MODEL_ARMOR_CREDENTIALS_JSON", "").strip()

# Si Model Armor no responde: false (default) deja pasar el mensaje; las capas locales
# de input_guardrails/ ya lo revisaron. Es la lección de Groq (2026-10-22): un filtro
# externo caído no puede dejar sin respuesta a quien escribe "1".
MODEL_ARMOR_FAIL_CLOSED = os.getenv("MODEL_ARMOR_FAIL_CLOSED", "false").strip().lower() in (
    "1", "true", "yes", "on", "si", "sí",
)
MODEL_ARMOR_TIMEOUT_SECONDS = float(os.getenv("MODEL_ARMOR_TIMEOUT_SECONDS", "5"))


def esta_configurado() -> bool:
    return bool(MODEL_ARMOR_PROJECT_ID and MODEL_ARMOR_LOCATION and MODEL_ARMOR_TEMPLATE_ID)


def nombre_plantilla() -> str:
    return (f"projects/{MODEL_ARMOR_PROJECT_ID}/locations/{MODEL_ARMOR_LOCATION}"
            f"/templates/{MODEL_ARMOR_TEMPLATE_ID}")
