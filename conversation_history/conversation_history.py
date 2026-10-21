"""
Backend de persistencia de conversación (memoria del agente) — PostgreSQL.
============================================================================

Provee el CHECKPOINTER que `create_agent` (LangGraph) usa para recordar el
historial de cada conversación entre invocaciones. La memoria se indexa por
`thread_id` (en este proyecto, el número de WhatsApp que llega por SmartGob Conecta o la sesión del CLI) y se
guarda en PostgreSQL, de modo que sobrevive a reinicios del proceso — deja de
ser efímera (`InMemorySaver`).

Esta carpeta es el ÚNICO lugar que conoce el backend de memoria: cambiar de
Postgres a Redis/SQLite solo debería tocar este módulo (todos son
`BaseCheckpointSaver` de LangGraph). `agent.py` únicamente llama a
`get_checkpointer()` y lo pasa a `create_agent(checkpointer=...)`.

Por qué un CHECKPOINTER y no `PostgresChatMessageHistory`:
    `create_agent` es un grafo de LangGraph; su persistencia idiomática es un
    checkpointer (guarda TODO el estado del grafo por `thread_id`), no un
    `BaseChatMessageHistory` (patrón legacy de `RunnableWithMessageHistory`).
    API verificada contra la doc oficial (MCP docs-langchain):
    `langgraph.checkpoint.postgres.PostgresSaver` + `create_agent(checkpointer=)`.

AISLAMIENTO POR ESQUEMA (`DB_SCHEMA`) — separar el histórico de cada campaña:
    `PostgresSaver` tiene los nombres de sus 4 tablas HARDCODEADOS en su SQL
    (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
    `checkpoint_migrations`) y su constructor NO acepta prefijo ni esquema
    (verificado con `inspect` contra langgraph-checkpoint-postgres 3.1.0). Por eso
    NO se puede pedir tablas llamadas `campana_madeline_checkpoints`.
    Lo que sí se puede —y es mejor— es ponerlas en un ESQUEMA propio: su DDL crea
    las tablas SIN calificar esquema, así que el `search_path` de la conexión
    decide dónde viven. Con `DB_SCHEMA=campana_toto_reyes` el histórico de esta
    campaña queda en `campana_toto_reyes.checkpoints`, totalmente separado del de
    otra campaña que use la misma base (p. ej. otra campaña de la agencia en `public`).
    Detalles de la implementación:
      - El `search_path` se fija con el parámetro de conexión `options`, y a
        propósito NO incluye `public` como respaldo: si lo incluyera y las tablas
        de este esquema aún no existieran, un SELECT caería en las tablas de
        `public` y leeríamos el histórico de OTRA campaña.
      - La librería no crea esquemas, así que este módulo hace el
        `CREATE SCHEMA IF NOT EXISTS` antes de abrir el pool (el nombre se valida
        contra una lista blanca de caracteres y se cita como identificador).
      - Sin `DB_SCHEMA` el comportamiento es el de siempre (esquema `public`).
      - ⚠️ CON POOLER EN MODO TRANSACCIÓN (Supabase/Supavisor, puerto 6543) hay que
        VERIFICAR que el pooler respete el `options` del cliente; algunos lo
        ignoran. Si no lo respeta, la alternativa robusta es fijarlo del lado del
        servidor, que ningún pooler puede ignorar:
            ALTER ROLE <usuario> IN DATABASE <base> SET search_path = campana_toto_reyes;
        Comprobación rápida tras arrancar: `select current_schema()` debe devolver
        el esquema esperado, y las 4 tablas deben aparecer ahí.

Por qué un `ConnectionPool` y no una `Connection` única:
    `main_conecta.py` invoca al agente en un threadpool (requests HTTP
    concurrentes del webhook). Una `Connection` de psycopg NO es segura para uso
    concurrente entre hilos; el pool entrega una conexión por operación. El
    pool exige `autocommit=True` y `row_factory=dict_row` (requisito de
    `PostgresSaver`) y `prepare_threshold=None` (DESACTIVA prepared statements:
    obligatorio con el pooler de Supabase en modo transacción/6543, donde los
    prepared statements con nombre fijo chocan; seguro también en sesión/5432).

Autor: Ing. Kevin Inofuente Colque - DataPath
"""

import os
import threading
from urllib.parse import quote_plus

from dotenv import load_dotenv, find_dotenv
from langgraph.checkpoint.base import BaseCheckpointSaver

load_dotenv(find_dotenv())

# ============================================
# 1. CONFIGURACIÓN DE BASE DE DATOS
# ============================================
# Se admite una URL completa (DATABASE_URL) o las piezas sueltas (DB_*).
# La URL completa tiene prioridad si está definida.
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")

# Tamaño máximo del pool de conexiones (requests concurrentes del webhook).
# El default era 10 por el tope de 15 CLIENTES del pooler de Supabase en modo SESIÓN
# (5432) el presupuesto era ajustado y pasarse daba EMAXCONNSESSION. Hoy el otro pool
# es el del registro (`simpatizantes/repositorio.py`, SIMPATIZANTES_DB_POOL_MAX).
# Desde el paso a modo TRANSACCIÓN (6543) ese tope ya no aplica. El checkpointer es el
# consumidor MÁS intenso: LangGraph lee y escribe estado en CADA super-step, así que un
# turno con varias tools son ~12 acquires solo de memoria.
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "20"))

# Espera máxima por una conexión (default de psycopg_pool: 30 s). Al saturarse el pool,
# cada operación colgaba medio minuto, y el turno completo se iba a minutos. Ver la nota
# equivalente en simpatizantes/repositorio.py.
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10"))

# Esquema de Postgres donde viven las 4 tablas del checkpointer. Vacío o "public" ->
# comportamiento histórico. Para este proyecto: `campana_toto_reyes`, así el histórico de
# conversación de esta campaña no se mezcla con el de otra en la misma base.
DB_SCHEMA = os.getenv("DB_SCHEMA", "").strip()

def _esquema_activo() -> str | None:
    """Esquema a usar, o None si se queda en el comportamiento por defecto (public).

    El nombre se interpola en un CREATE SCHEMA (DDL, donde no se pueden usar
    parámetros), así que se valida con el validador de la skill
    `postgres-tenant-schema-naming` (`validacion_esquema_tenant.py` en la raíz) —
    la MISMA fuente de verdad que usa `simpatizantes/repositorio.py`, para que no se separen.
    """
    if not DB_SCHEMA or DB_SCHEMA.lower() == "public":
        return None
    from validacion_esquema_tenant import validar_esquema

    v = validar_esquema(DB_SCHEMA)
    if not v.ok:
        raise ValueError(f"DB_SCHEMA inválido: {'; '.join(v.motivos)}")
    return DB_SCHEMA


def _asegurar_esquema(url: str, esquema: str) -> None:
    """Crea el esquema si no existe. `PostgresSaver.setup()` NO crea esquemas.

    Usa una conexión efímera SIN `search_path` (el esquema todavía puede no existir) y
    la cierra enseguida. Idempotente.
    """
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("create schema if not exists {}").format(sql.Identifier(esquema))
        )


def _database_url() -> str:
    """Arma la URL de conexión a Postgres, o '' si no hay config suficiente.

    Prioriza `DATABASE_URL`; si no existe, la ensambla desde las piezas `DB_*`.
    Devuelve cadena vacía cuando faltan datos -> el agente cae a memoria
    efímera (útil para pruebas locales sin Postgres).
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    if not all([DB_USER, DB_PASSWORD, DB_HOST]):
        return ""
    return (
        f"postgresql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )


# ============================================
# 2. CHECKPOINTER (memoria persistente del agente)
# ============================================
# Singleton a nivel de proceso: el pool y las tablas se crean UNA sola vez,
# aunque `construir_agente()` se llame varias veces. El lock lo blinda si dos
# hilos llamaran a `get_checkpointer()` a la vez (aunque lo normal es que se
# inicialice en el arranque, single-thread, antes de aceptar tráfico).
_checkpointer: BaseCheckpointSaver | None = None
_lock = threading.Lock()


def get_checkpointer() -> BaseCheckpointSaver:
    """Devuelve el checkpointer del agente (PostgresSaver persistente).

    Si hay configuración de Postgres, abre un pool de conexiones, crea las
    tablas de LangGraph si no existen (`setup()`, idempotente) y devuelve un
    `PostgresSaver`. Si NO hay configuración, cae a `InMemorySaver` (memoria
    efímera) con una advertencia — así `main.py` sigue siendo probable en local
    sin una base de datos.
    """
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    with _lock:
        # Doble chequeo: otro hilo pudo haberlo creado mientras esperábamos.
        if _checkpointer is not None:
            return _checkpointer

        url = _database_url()
        if not url:
            # Sin Postgres configurado: memoria efímera (solo pruebas locales).
            from langgraph.checkpoint.memory import InMemorySaver

            print(
                "⚠️  Sin configuración de Postgres (DATABASE_URL o DB_USER/DB_PASSWORD/"
                "DB_HOST). La memoria será EFÍMERA (InMemorySaver): no sobrevive a "
                "reinicios. Definí las variables de base de datos para persistir."
            )
            _checkpointer = InMemorySaver()
            return _checkpointer

        # Import perezoso: psycopg/PostgresSaver solo en modo persistente.
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver

        # Esquema propio (opcional): aísla el histórico de ESTA campaña. Se crea antes
        # de abrir el pool, porque el pool ya viene con el search_path apuntando ahí.
        esquema = _esquema_activo()
        conn_kwargs = {
            "autocommit": True,
            "prepare_threshold": None,
            "row_factory": dict_row,
        }
        if esquema:
            _asegurar_esquema(url, esquema)
            # Sin `,public` a propósito: ver la nota del docstring (evita leer por
            # error las tablas de otra campaña mientras este esquema esté vacío).
            conn_kwargs["options"] = f"-c search_path={esquema}"

        destino = f"esquema '{esquema}'" if esquema else "esquema por defecto (public)"
        print(f"🔌 Memoria persistente en Postgres · {destino} "
              f"(pool máx={DB_POOL_MAX}, espera máx={DB_POOL_TIMEOUT}s).")

        pool = ConnectionPool(
            conninfo=url,
            max_size=DB_POOL_MAX,
            timeout=DB_POOL_TIMEOUT,
            open=True,
            # `check` valida cada conexión antes de entregarla: si el pooler
            # gestionado (Supabase/PgBouncer) recicló o cortó una conexión
            # ociosa, el pool la reemplaza en vez de fallar en la operación.
            check=ConnectionPool.check_connection,
            # Requisitos de PostgresSaver + seguridad con poolers externos.
            # prepare_threshold=None DESACTIVA prepared statements: obligatorio en el
            # pooler de Supabase en modo TRANSACCIÓN (6543), donde las conexiones se
            # multiplexan y los prepared statements con nombre fijo chocan
            # ("DuplicatePreparedStatement"). Seguro también en modo sesión (5432).
            # `options` (search_path) se agrega arriba solo si hay DB_SCHEMA.
            kwargs=conn_kwargs,
        )
        saver = PostgresSaver(pool)
        saver.setup()  # Crea las tablas de checkpoints si no existen (idempotente).

        _checkpointer = saver
        return _checkpointer


def close_checkpointer() -> None:
    """Cierra el pool de conexiones del checkpointer (para el shutdown del canal).

    Idempotente y tolerante: si la memoria era efímera (`InMemorySaver`) o ya
    estaba cerrada, no hace nada. Llamar al apagar el servidor (p. ej. tras el
    `yield` del lifespan de FastAPI) para liberar las conexiones — importante
    con un pooler gestionado, donde las conexiones colgadas cuentan contra el
    cupo de la cuenta.
    """
    global _checkpointer
    with _lock:
        # El pool queda accesible como `.conn` del PostgresSaver.
        pool = getattr(_checkpointer, "conn", None)
        cerrar = getattr(pool, "close", None)
        if callable(cerrar):
            try:
                cerrar()
            except Exception as e:  # noqa: BLE001 — cierre best-effort
                print(f"⚠️ Nota al cerrar el pool de memoria: {e}")
        _checkpointer = None
