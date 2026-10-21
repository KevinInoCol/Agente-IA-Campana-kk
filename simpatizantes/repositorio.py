"""
Repositorio de simpatizantes (leads del flujo "Quiero sumarme") en PostgreSQL.
==============================================================================

Único lugar que sabe cómo se guarda un simpatizante en la base. La tool
`guardar_simpatizante` lo llama; el canal no lo conoce.

La conexión sale de las MISMAS variables que la memoria del agente (`DATABASE_URL`
o `DB_*`) y del mismo esquema (`DB_SCHEMA`), así la tabla de leads y las tablas del
checkpointer viven juntas en `campana_toto_reyes`. Por eso se reutilizan los helpers
de `conversation_history/` en vez de repetirlos: si mañana cambia cómo se arma la URL
o cómo se valida el esquema, cambia en un solo sitio.

La tabla se crea sola en el primer uso ejecutando `migrations/001_simpatizantes.sql`
con el esquema activo. El SQL vive en un solo lugar (el archivo de migración): el
mismo que se pega en Supabase es el que corre aquí.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, find_dotenv
from psycopg import sql

from conversation_history.conversation_history import (
    _asegurar_esquema,
    _database_url,
    _esquema_activo,
)

load_dotenv(find_dotenv())

# Pool propio, chico: un lead se guarda una vez por conversación, el que trabaja de
# verdad es el del checkpointer.
SIMPATIZANTES_DB_POOL_MAX = int(os.getenv("SIMPATIZANTES_DB_POOL_MAX", "3"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10"))

MIGRACION = Path(__file__).resolve().parent.parent / "migrations" / "001_simpatizantes.sql"
# Esquema con el que está escrito el archivo de migración (para pegarlo en Supabase).
ESQUEMA_EN_MIGRACION = "campana_toto_reyes"

_pool = None
_lock = threading.Lock()


@dataclass
class Simpatizante:
    nombre: str
    barrio: str
    ayuda: str
    telefono: str | None = None
    nombre_perfil: str | None = None
    canal: str = "whatsapp"


def esta_configurado() -> bool:
    """True si hay base de datos configurada para guardar simpatizantes."""
    return bool(_database_url())


def _tabla() -> sql.Composed:
    esquema = _esquema_activo() or "public"
    return sql.SQL("{}.simpatizantes").format(sql.Identifier(esquema))


def _crear_tabla(conn) -> None:
    """Ejecuta la migración 001 apuntando al esquema activo (idempotente)."""
    esquema = _esquema_activo() or "public"
    ddl = MIGRACION.read_text(encoding="utf-8")
    if esquema != ESQUEMA_EN_MIGRACION:
        # El nombre ya pasó por `validar_esquema` en `_esquema_activo()`.
        ddl = ddl.replace(ESQUEMA_EN_MIGRACION, esquema)
    conn.execute(ddl)


def _get_pool():
    """Pool de conexiones (singleton). Crea el esquema y la tabla la primera vez."""
    global _pool
    if _pool is not None:
        return _pool
    with _lock:
        if _pool is not None:
            return _pool
        url = _database_url()
        if not url:
            raise RuntimeError("Sin base de datos configurada (DATABASE_URL o DB_*).")

        from psycopg_pool import ConnectionPool

        esquema = _esquema_activo()
        if esquema:
            _asegurar_esquema(url, esquema)
        pool = ConnectionPool(
            conninfo=url,
            # min_size por defecto es 4: con un máximo chico hay que bajarlo también.
            min_size=1,
            max_size=SIMPATIZANTES_DB_POOL_MAX,
            timeout=DB_POOL_TIMEOUT,
            open=True,
            check=ConnectionPool.check_connection,
            # Sin prepared statements: compatible con el pooler de Supabase (6543).
            kwargs={"autocommit": True, "prepare_threshold": None},
        )
        with pool.connection() as conn:
            _crear_tabla(conn)
        _pool = pool
        return _pool


def guardar(simpatizante: Simpatizante) -> int:
    """Inserta un simpatizante y devuelve su id. Lanza excepción si falla."""
    consulta = sql.SQL(
        "insert into {} (telefono, nombre, barrio, ayuda, nombre_perfil, canal) "
        "values (%s, %s, %s, %s, %s, %s) returning id"
    ).format(_tabla())
    with _get_pool().connection() as conn:
        fila = conn.execute(
            consulta,
            (
                simpatizante.telefono,
                simpatizante.nombre,
                simpatizante.barrio,
                simpatizante.ayuda,
                simpatizante.nombre_perfil,
                simpatizante.canal,
            ),
        ).fetchone()
    return int(fila[0])


def cerrar() -> None:
    """Cierra el pool (shutdown del canal). Idempotente."""
    global _pool
    with _lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception as e:  # noqa: BLE001 — cierre best-effort
                print(f"⚠️ Nota al cerrar el pool de simpatizantes: {e}")
        _pool = None
