"""
Agente de WhatsApp de Juan José "Toto" Reyes Baquerizo (LangChain `create_agent`)
=================================================================================

Asistente de la campaña "Toto Reyes · Concejal" (Guayaquil). Presenta al candidato
siguiendo el guion aprobado por el cliente y capta simpatizantes con el flujo
"Quiero sumarme".

Este archivo solo ORQUESTA. Cada pieza vive en su carpeta:
  - Prompt (formato TAGS)      -> prompt/prompt.yaml
  - Configuración del LLM      -> model_config/model_config.yaml
  - Tools                      -> tools/ (guardar_simpatizante)
  - Memoria de conversación    -> conversation_history/ (PostgresSaver, por thread_id)
  - Guardrail de entrada       -> input_guardrails/ (middleware before_agent)
  - Observabilidad             -> observabilty/ (Langfuse, trace por CallbackHandler)

Sin RAG: todo lo que el agente puede decir del candidato es el guion del cliente,
que cabe entero en el prompt. Si el cliente entrega más material (trayectoria,
propuestas), ahí sí conviene una tool de retrieval sobre Qdrant
(`tenant_id_campana_toto_reyes`, skill rag-tenant-collection-naming).

Sin PIIMiddleware: lo que el agente recoge es nombre, barrio y una idea, y el
detector de direcciones/lugares redactaba justo el barrio (lo vimos en AVANZA). El
teléfono no pasa por el modelo: viaja en el ConversacionContext. En Langfuse, la
MaskFunction de observabilty/ redacta cédulas y teléfonos.
"""

import hashlib
import hmac
import os
from contextlib import contextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv, find_dotenv

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

from agent_context import ConversacionContext
from conversation_history import get_checkpointer
from input_guardrails import construir_middleware_guardrail
from middleware import EstadoRegistroMiddleware
from tools import guardar_simpatizante, registrar_ciudadano

load_dotenv(find_dotenv())

# Interruptor de OBSERVABILIDAD (traces). false -> no se importa ni arranca Langfuse.
LANGFUSE_TRACING_ENABLED = os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on", "si", "sí",
)

# Identidad del tenant: la misma que gobierna el esquema de Postgres y el namespace de
# Redis. Etiqueta las trazas para separar costos cuando el proyecto de Langfuse se
# comparte entre campañas, y entra en la sal del seudónimo.
TENANT = (os.getenv("LANGFUSE_TENANT_TAG") or os.getenv("DB_SCHEMA") or "").strip()

BASE_DIR = Path(__file__).resolve().parent

# Las tools cableadas. Constante de módulo para que /health las reporte.
TOOLS_CABLEADAS = [
    registrar_ciudadano,    # ACCIÓN  registro de la bienvenida (solo los datos que faltan)
    guardar_simpatizante,   # ACCIÓN  cierre del flujo "Quiero sumarme"
]

_langfuse_handler = None
_propagate_attributes = None
if LANGFUSE_TRACING_ENABLED:
    try:
        from langfuse import propagate_attributes as _propagate_attributes  # noqa: F811
        from langfuse.langchain import CallbackHandler
        from observabilty.langfuse_setup import langfuse_client  # noqa: F401 — arranca el cliente
        _langfuse_handler = CallbackHandler()
    except Exception as e:  # noqa: BLE001 — nunca romper el arranque por la observabilidad
        print(f"⚠️  Langfuse tracing no disponible ({e}); el agente sigue sin traces.")
        _langfuse_handler = None
        _propagate_attributes = None


def cargar_prompt() -> str:
    """System prompt desde prompt/prompt.yaml."""
    data = yaml.safe_load((BASE_DIR / "prompt" / "prompt.yaml").read_text(encoding="utf-8"))
    return (data.get("system_prompt") or "").strip()


def cargar_model_config() -> dict:
    """Configuración del LLM desde model_config/model_config.yaml."""
    return yaml.safe_load(
        (BASE_DIR / "model_config" / "model_config.yaml").read_text(encoding="utf-8")
    )


def _seudonimo(valor: str | None) -> str | None:
    """HMAC del thread_id (que es el teléfono) antes de mandarlo a Langfuse.

    Estable: el mismo número cae siempre en el mismo seudónimo y las sesiones se
    agrupan bien. El TENANT entra en la sal para que el mismo número no produzca el
    mismo seudónimo en dos campañas que compartan el proyecto de Langfuse. La
    resistencia a fuerza bruta depende de que la sal sea secreta
    (LANGFUSE_SECRET_KEY o PSEUDONYM_SALT).
    """
    if not valor:
        return valor
    base = os.getenv("LANGFUSE_SECRET_KEY") or os.getenv("PSEUDONYM_SALT") or "toto-reyes"
    salt = f"{base}|{TENANT}".encode() if TENANT else base.encode()
    return "s_" + hmac.new(salt, valor.encode(), hashlib.sha256).hexdigest()[:16]


@contextmanager
def traza(session_id: str | None = None):
    """Agrupa la corrida en Langfuse por sesión (seudonimizada) y la etiqueta con el
    tenant. NO-OP si el tracing está apagado."""
    if _langfuse_handler is not None and _propagate_attributes is not None:
        atributos = {"session_id": _seudonimo(session_id), "user_id": _seudonimo(session_id)}
        if TENANT:
            atributos["tags"] = [TENANT]
            atributos["metadata"] = {"tenant": TENANT}
        with _propagate_attributes(**atributos):
            yield
    else:
        yield


def flush_langfuse() -> None:
    """Vacía los traces pendientes al apagar. NO-OP si el tracing está apagado."""
    if _langfuse_handler is None:
        return
    try:
        from observabilty.langfuse_setup import langfuse_client
        langfuse_client.flush()
    except Exception:  # noqa: BLE001 — best-effort al apagar
        pass


def construir_agente():
    """Construye el agente: LLM + tools + prompt + guardrail + memoria persistente."""
    cfg = cargar_model_config()

    model = init_chat_model(
        f"{cfg['provider']}:{cfg['model']}",
        temperature=cfg.get("temperature", 0),
        max_tokens=cfg.get("max_tokens"),
    )

    agente = create_agent(
        model=model,
        # Si se agrega una tool aquí, documentarla en <herramientas> del prompt en la
        # misma tanda, y al revés: el prompt no puede nombrar una tool que no esté acá.
        tools=TOOLS_CABLEADAS,
        system_prompt=cargar_prompt(),
        # 1) El guardrail corta inyecciones, toxicidad y secretos ANTES del LLM.
        # 2) EstadoRegistro le dice al modelo qué datos ya tiene la persona en la base,
        #    para que pida solo los que faltan (pedido del cliente, guion v1).
        middleware=[construir_middleware_guardrail(), EstadoRegistroMiddleware()],
        checkpointer=get_checkpointer(),
        context_schema=ConversacionContext,
    )
    if _langfuse_handler is not None:
        return agente.with_config({"callbacks": [_langfuse_handler]})
    return agente
