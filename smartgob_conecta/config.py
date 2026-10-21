"""
Configuración del canal SmartGob Conecta (WhatsApp).
====================================================

Único lugar que lee las variables `SMARTGOB_*`. Carga su propio `.env` porque sus
valores se leen a nivel de módulo (mismo patrón que `debounce/config.py`).

SmartGob Conecta es la plataforma de Origami que tiene conectado el WhatsApp del
candidato (corre Evolution API por debajo, en modo Baileys). El cliente pidió que el
agente use ESTA API y no Evolution directo: Conecta no expone las rutas de Evolution.

Contrato verificado contra `{SMARTGOB_API_URL}/openapi.json` (v1.0.0, 2026-10-21):
  - Auth: `Authorization: Bearer sgc_<prefijo>_<secreto>` (credencial de integración,
    una por organización, con alcances: messages:send, messages:read, ...).
  - Enviar: POST /messages {connectionId, to | contactId, text} + cabecera
    `Idempotency-Key` OBLIGATORIA (8-128 caracteres). `202` = registrado, no entregado.
  - Archivos recibidos: GET /files/{id}/content (alcance messages:read). El
    `mediaFileId` aparece en el mensaje cuando `mediaState` es STORED.
  - Webhooks salientes: `message.received` (y otros). Cada POST trae
    `X-Conecta-Event-Id`, `X-Conecta-Event-Type` y
    `X-Conecta-Signature: t=<unix>,v1=<hex>`, con v1 = HMAC-SHA256(secreto,
    "<t>.<cuerpo crudo>"). Hay que verificar la firma, rechazar marcas de más de
    5 minutos, deduplicar por event id y responder 2xx en menos de 10 s (reintenta
    hasta 8 veces).
"""

import os

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

SMARTGOB_API_URL = os.getenv(
    "SMARTGOB_API_URL", "https://smartconecta.origamismartgob.com/api/v1"
).strip().rstrip("/")
# Credencial de integración (sgc_…). Es la de `Datos/key-conexion.txt`.
SMARTGOB_API_KEY = os.getenv("SMARTGOB_API_KEY", "").strip()
# Conexión de WhatsApp del candidato ("TOTO REYES"). Se obtiene con GET /connections.
SMARTGOB_CONNECTION_ID = os.getenv("SMARTGOB_CONNECTION_ID", "").strip()

# Secreto de firma del webhook. Lo muestra el panel UNA sola vez al crear el webhook
# (Administración -> Integraciones -> Webhooks).
SMARTGOB_WEBHOOK_SECRET = os.getenv("SMARTGOB_WEBHOOK_SECRET", "").strip()

# Sin secreto, el webhook RECHAZA todo (fail-closed). Aceptar sin firma solo se permite
# en desarrollo y a propósito: con la key real, un webhook abierto deja que cualquiera
# haga que el bot escriba a números arbitrarios desde el WhatsApp del candidato.
SMARTGOB_ALLOW_UNSIGNED = os.getenv("SMARTGOB_ALLOW_UNSIGNED", "false").strip().lower() in (
    "1", "true", "yes", "on", "si", "sí",
)

# Antigüedad máxima aceptada de la firma (la API pide rechazar más de 5 minutos).
TOLERANCIA_FIRMA_SEGUNDOS = 300

TIMEOUT_SEGUNDOS = float(os.getenv("SMARTGOB_TIMEOUT_SECONDS", "20"))


def esta_configurado() -> bool:
    return bool(SMARTGOB_API_URL and SMARTGOB_API_KEY and SMARTGOB_CONNECTION_ID)
