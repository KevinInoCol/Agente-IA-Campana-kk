"""
Tool: buscar en la BASE DE CONOCIMIENTO aprobada (Qdrant) — agente de Toto Reyes.
==================================================================================

Copiada de MAD, donde corre en producción. Busca semánticamente en la colección que
ingiere `rag/rag.py`: el perfil público de Juan José Reyes Baquerizo (biografía,
formación, trayectoria, proyectos, causas y valores).

⚠️ TODAVÍA NO ESTÁ CABLEADA AL AGENTE (2026-10-21): la colección no existe porque
   la Ayuda Memoria que entregó el cliente es la plantilla vacía. Para activarla,
   cuando `rag/rag.py` haya cargado el documento completo:
     1. Agregar `buscar_conocimiento` a `TOOLS_CABLEADAS` en `agent.py`.
     2. Documentarla en <herramientas> de `prompt/prompt.yaml` y ajustar
        <reglas_centrales>: hoy dicen que todo sale de <guion>.
     3. Subir la `version` del prompt.

Qué NO se responde con esta tool:
    Nada DINÁMICO (agenda, eventos, fechas): en Qdrant quedaría congelado y el
    agente afirmaría cosas desactualizadas.

Contrato de salida — ENVELOPE
-------------------------------------------------
Devuelve un `dict` (no una cadena) con la misma forma que en MAD:

    {"ok": bool, "data": {...}|None, "error": {...}|None,
     "meta": {"correlation_id", "source", "schema_version"}}

Devolver un objeto desde una `@tool` es el patrón idiomático de LangChain v1
("Return an object when your tool produces structured data that the model should
inspect: the object is serialized and sent back as tool output"), verificado en
la doc oficial vía el MCP `docs-langchain`. Importa que `ok` viaje como campo
explícito: la regla central del prompt es "nunca confirmes una acción si la tool
no devolvió ok=true", y el modelo necesita LEER ese campo, no adivinarlo.

Además del envelope, `data.instruccion` lleva una frase de guía en prosa (qué
hacer con el resultado). Es el estilo que ya usan las otras tools del repo
(`PARROQUIA_VALIDA: ... confírmalo y marca ...`) y en la práctica el modelo
obedece mucho mejor una instrucción explícita que un JSON a secas.

UMBRAL DE RELEVANCIA (`RETRIEVAL_MIN_SCORE`, por defecto 0.35)
--------------------------------------------------------------
Sin umbral, Qdrant SIEMPRE devuelve los `TOP_K` vecinos más cercanos, aunque no
tengan nada que ver: preguntando por el teléfono personal de Madeline —que no
está en la base— devolvía 4 fragmentos con score ~0.29, y el modelo recibía
contexto plausible para una pregunta sin respuesta. Eso choca de frente con la
regla "no inventes, deriva". Con el umbral, esas consultas devuelven
`ok=true` + `data.fragmentos == []` + una instrucción explícita de derivar.
El valor 0.35 es empírico sobre la colección de MAD con text-embedding-3-small
(consultas legítimas: 0.39-0.65; la irrelevante: 0.29). Con text-embedding-3-large
y otro corpus los scores cambian: RECALIBRARLO tras la primera ingesta.

Embeddings: DEBEN ser el MISMO modelo que usó la ingesta (`EMBEDDING_MODEL`). Si
divergen, los vectores viven en espacios distintos y el retrieval devuelve
resultados sin sentido — sin fallar, que es lo peligroso.

Es un lookup PURO: no recibe `ToolRuntime` porque no necesita el `contact_id`
(igual que `validar_cedula`). No escribe nada.
"""

import json
import os
import uuid
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import load_dotenv, find_dotenv

from langchain.tools import tool
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient

# La tool se auto-abastece del .env: este módulo se importa antes de que
# agent.py cargue el entorno, y sus variables se leen a nivel de módulo.
_ = load_dotenv(find_dotenv())

# --- Configuración leída del entorno (.env); defaults idénticos a rag/rag.py ---
# Conexión: si hay QDRANT_URL (Qdrant gestionado/producción) se usa URL + API key;
# si no, se cae a host+port (Qdrant local en Docker).
QDRANT_URL = os.getenv("QDRANT_URL")
# El nombre canónico es QDRANT_API_KEY; se acepta QDRAN_API_KEY (typo) como respaldo.
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or os.getenv("QDRAN_API_KEY")
if not os.getenv("QDRANT_API_KEY") and os.getenv("QDRAN_API_KEY"):
    print("⚠️  Usando QDRAN_API_KEY (typo). Renombrá la variable a QDRANT_API_KEY en tu .env.")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "tenant_id_campana_toto_reyes")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "4"))
MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.35"))

# Identidad de esta tool en el envelope (`meta.source`) y versión del contrato.
TOOL_NAME = "buscar_conocimiento"
SCHEMA_VERSION = "v1"


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    if QDRANT_URL:
        parsed = urlparse(QDRANT_URL)
        # Si la URL no trae puerto explícito y es https, usar 443 (proxy HTTPS
        # estándar, p. ej. easypanel) en vez del default 6333 de qdrant-client.
        # Si la URL YA trae puerto (p. ej. Qdrant Cloud :6333), se respeta.
        if parsed.port is None and parsed.scheme == "https":
            return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, port=443)
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


@lru_cache(maxsize=1)
def _embeddings() -> OpenAIEmbeddings:
    # Mismo modelo de embeddings que la ingesta (EMBEDDING_MODEL, hoy 3-large).
    return OpenAIEmbeddings(model=EMBEDDING_MODEL)


def _extract_text(payload: dict) -> str:
    """Extrae el texto del payload, sea formato de rag.py, LangChain o LlamaIndex."""
    if not payload:
        return ""
    for key in ("text", "page_content", "content"):
        if payload.get(key):
            return payload[key]
    node_content = payload.get("_node_content")  # LlamaIndex guarda el nodo como JSON
    if node_content:
        try:
            return json.loads(node_content).get("text", "")
        except (json.JSONDecodeError, TypeError):
            pass
    return json.dumps(payload, ensure_ascii=False)


def _meta() -> dict:
    """Bloque `meta` del envelope: trazabilidad de la llamada."""
    return {
        "correlation_id": str(uuid.uuid4()),
        "source": TOOL_NAME,
        "schema_version": SCHEMA_VERSION,
    }


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data, "error": None, "meta": _meta()}


def _fallo(code: str, message_safe: str, retryable: bool) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message_safe": message_safe, "retryable": retryable},
        "meta": _meta(),
    }


@tool
def buscar_conocimiento(consulta: str) -> dict:
    """Busca en la base de conocimiento APROBADA sobre Juan José Reyes Baquerizo.

    Úsala para preguntas sobre el candidato que el guion no responde: su
    biografía, su familia, su formación, su trayectoria en el servicio público,
    sus proyectos sociales, sus causas y sus valores.

    `consulta`: la pregunta de la persona, con sus propias palabras.

    NO la uses para datos que CAMBIAN (eventos, fechas, agenda): no viven aquí.

    Devuelve un objeto con `ok`, `data` y `error`:
    - `ok=true` con `data.fragmentos` NO vacío -> responde APOYÁNDOTE ÚNICAMENTE en
      esos fragmentos, en tercera persona y sin agregar nada de tu memoria.
    - `ok=true` con `data.fragmentos` VACÍO -> la base NO tiene esa información:
      dilo con honestidad y deriva a una persona del equipo. NUNCA la inventes.
    - `ok=false` -> hubo un fallo técnico: NO afirmes nada sobre el tema y deriva.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return _fallo("EMPTY_QUERY", "Consulta vacía: no hay nada que buscar.", False)

    try:
        vector = _embeddings().embed_query(consulta)
    except Exception as e:  # noqa: BLE001 — no filtrar detalles del proveedor al LLM
        return _fallo("EMBEDDING_UNAVAILABLE",
                      f"No se pudo generar el embedding de la consulta: {type(e).__name__}", True)

    try:
        respuesta = _client().query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            limit=TOP_K,
            score_threshold=MIN_SCORE,   # Qdrant descarta los vecinos irrelevantes
            with_payload=True,
        )
    except Exception as e:  # noqa: BLE001
        return _fallo("KNOWLEDGE_BASE_UNAVAILABLE",
                      f"No se pudo consultar la base de conocimiento: {type(e).__name__}", True)

    fragmentos = []
    for punto in respuesta.points:
        payload = punto.payload or {}
        texto = _extract_text(payload).strip()
        if not texto:
            continue
        fragmentos.append(
            {
                "texto": texto,
                "titulo": payload.get("titulo"),
                "seccion": payload.get("seccion"),
                "pagina": payload.get("page"),
                "fuente": payload.get("source"),
                "score": round(punto.score, 3),
            }
        )

    if not fragmentos:
        return _ok(
            {
                "fragmentos": [],
                "total": 0,
                "instruccion": (
                    "La base de conocimiento NO tiene información sobre esa consulta. "
                    "Responde con el texto FUERA_DE_ALCANCE del guion y ofrece el menú. "
                    "NUNCA inventes la información."
                ),
            }
        )

    return _ok(
        {
            "fragmentos": fragmentos,
            "total": len(fragmentos),
            "instruccion": (
                "Responde usando ÚNICAMENTE estos fragmentos, con tus palabras, breve y "
                "cálido, hablando de Juan José en TERCERA persona. No agregues datos que no "
                "estén aquí. No menciones la base de conocimiento, los fragmentos, los "
                "scores ni ningún detalle técnico."
            ),
        }
    )
