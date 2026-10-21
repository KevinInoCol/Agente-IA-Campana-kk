"""
Inicialización del cliente de Langfuse (observabilidad + prompt management).
============================================================================

Único lugar que arranca Langfuse. Expone `langfuse_client` (el cliente singleton
v4, basado en OpenTelemetry) que usan:
  - `agent.py` -> prompt management (`get_prompt`) y el trace del agente.
  - los canales -> `flush()` al apagar para no perder traces.

IMPORTANTE — carga del `.env` acá:
    Este módulo se importa desde `agent.py` ANTES de que `agent.py` corra su
    propio `load_dotenv`. Si no cargáramos el entorno aquí, `get_client()` se
    inicializaría sin las claves (mismo patrón que la tool de retrieval). Por eso
    el `load_dotenv` va al inicio de ESTE archivo.

Variables de entorno (ver `.env.example`):
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY  -> credenciales del proyecto
    LANGFUSE_HOST (o LANGFUSE_BASE_URL)       -> URL del servidor Langfuse

Autor: Ing. Kevin Inofuente Colque - DataPath
"""

import os
import re

from dotenv import load_dotenv, find_dotenv
from langfuse import Langfuse, get_client

load_dotenv(find_dotenv())

# ---------------------------------------------------------------------------
# MaskFunction: redacta PII (cédula / teléfono EC) en el CONTENIDO de los traces
# (input/output/metadata) ANTES de exportarlo a Langfuse. Segunda capa sobre el
# PIIMiddleware del agente: el CallbackHandler traza el input CRUDO del invoke
# (antes de que el middleware redacte dentro del grafo), así que sin esto la
# cédula/teléfono llegarían en claro a Langfuse (LOPDP). La cédula COMPLETA sigue
# guardándose en Supabase por el canal; esto NO la toca — solo el trace.
# ---------------------------------------------------------------------------
# Teléfono EC primero (puede ser 10 díg. y chocaría con la cédula): +593/0 + 8-9 díg.
# Se usan lookarounds anti-dígito (?<!\d)/(?!\d) en vez de \b para cazar también el
# número PEGADO a letras ("CI1710034065") sin partir un número más largo.
_TEL_EC_RE = re.compile(r"(?:\+?593[\s\-]?|(?<!\d)0)\d{8,9}(?!\d)")
# Cédula/identificador de 10 dígitos (válida o no): red de seguridad amplia.
_CEDULA_RE = re.compile(r"(?<!\d)\d{10}(?!\d)")


def _redactar_texto(s: str) -> str:
    s = _TEL_EC_RE.sub("[REDACTED_TEL]", s)
    s = _CEDULA_RE.sub("[REDACTED_CEDULA]", s)
    return s


def mask_pii(*, data):
    """MaskFunction de Langfuse: redacta CÉDULA y TELÉFONO EC en el contenido de los
    traces (input/output/metadata), recorriendo dict/list/str recursivamente.

    ALCANCE (a propósito): cubre solo los DOS identificadores críticos de LOPDP
    (cédula, teléfono), que son los que el CallbackHandler ve CRUDOS en la raíz del
    trace antes de que el PIIMiddleware redacte dentro del grafo. NO cubre los otros
    3 tipos del PIIMiddleware (fecha de nacimiento, dirección, lugar de nacimiento):
    son detectores best-effort de texto libre y aquí se aceptan como riesgo residual
    (coherente con su fiabilidad). Solo redacta STRINGS: un dato numérico (int) no se
    toca (en traces de LLM el contenido es texto). Best-effort: ante error devuelve el
    dato original y nunca rompe el export. La cédula COMPLETA sigue en Supabase (esto
    no la toca)."""
    def walk(x):
        if isinstance(x, str):
            return _redactar_texto(x)
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [walk(v) for v in x]
        return x
    try:
        return walk(data)
    except Exception:  # noqa: BLE001 — nunca romper el export por el masking
        return data

# Host: el SDK lee LANGFUSE_HOST; aceptamos también LANGFUSE_BASE_URL por
# compatibilidad con el .env del proyecto. Default: Langfuse Cloud (región US).
_HOST = (
    os.getenv("LANGFUSE_HOST")
    or os.getenv("LANGFUSE_BASE_URL")
    or "https://us.cloud.langfuse.com"
)

# Inicializa el singleton con credenciales y host explícitos (get_client() no
# recibe host/secret; deben venir del entorno o de este constructor).
# `timeout` corto: acota el auth_check de abajo para que un host inalcanzable
# (blackhole) no cuelgue el arranque del agente/servidor.
Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=_HOST,
    timeout=5,
    mask=mask_pii,  # redacta cédula/teléfono en el contenido de los traces (LOPDP)
)

langfuse_client = get_client()

# Chequeo de credenciales: no aborta el arranque si falla (el agente igual
# funciona sin observabilidad), pero avisa claramente.
try:
    if langfuse_client.auth_check():
        print(f"📊 Langfuse conectado: {_HOST}")
    else:
        print(
            f"⚠️  Langfuse NO autenticado ({_HOST}). Verificá LANGFUSE_PUBLIC_KEY "
            "y LANGFUSE_SECRET_KEY. El agente seguirá funcionando sin traces."
        )
except Exception as e:  # noqa: BLE001 — el chequeo es best-effort
    print(f"⚠️  No se pudo verificar Langfuse ({_HOST}): {e}")
