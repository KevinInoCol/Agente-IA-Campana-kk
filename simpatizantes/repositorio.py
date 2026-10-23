"""
Repositorio de ciudadanos y simpatizantes en PostgreSQL.
========================================================

Único lugar que sabe cómo se guardan en la base:
  - `ciudadanos`: el registro de la bienvenida (cédula, nombres, correo, barrio),
    uno por número de WhatsApp. Lo escribe la tool `registrar_ciudadano`.
  - `simpatizantes`: los leads del flujo "Quiero sumarme" (uno por idea). Lo escribe
    la tool `guardar_simpatizante`.
El canal no conoce este módulo.

La conexión sale de las MISMAS variables que la memoria del agente (`DATABASE_URL`
o `DB_*`) y del mismo esquema (`DB_SCHEMA`), así la tabla de leads y las tablas del
checkpointer viven juntas en `campana_toto_reyes`. Por eso se reutilizan los helpers
de `conversation_history/` en vez de repetirlos: si mañana cambia cómo se arma la URL
o cómo se valida el esquema, cambia en un solo sitio.

Las tablas se crean solas en el primer uso ejecutando las migraciones numeradas de
`migrations/` (001, 002, ...) con el esquema activo. El SQL vive en un solo lugar (los
archivos de migración): el mismo que se pega en Supabase es el que corre aquí.
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

CARPETA_MIGRACIONES = Path(__file__).resolve().parent.parent / "migrations"
# 000_bootstrap_tenant.sql es una PLANTILLA para pegar a mano (toca roles): no corre aquí.
MIGRACIONES = sorted(p for p in CARPETA_MIGRACIONES.glob("[0-9][0-9][0-9]_*.sql")
                     if not p.name.startswith("000_"))
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


@dataclass
class Ciudadano:
    cedula: str
    nombres: str
    barrio: str
    correo: str | None = None
    telefono: str | None = None
    nombre_perfil: str | None = None
    canal: str = "whatsapp"


def esta_configurado() -> bool:
    """True si hay base de datos configurada para guardar simpatizantes."""
    return bool(_database_url())


def _tabla(nombre: str = "simpatizantes") -> sql.Composed:
    esquema = _esquema_activo() or "public"
    return sql.SQL("{}.{}").format(sql.Identifier(esquema), sql.Identifier(nombre))


def _crear_tablas(conn) -> None:
    """Ejecuta las migraciones en orden, apuntando al esquema activo (idempotentes)."""
    esquema = _esquema_activo() or "public"
    for migracion in MIGRACIONES:
        ddl = migracion.read_text(encoding="utf-8")
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
            _crear_tablas(conn)
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


def guardar_ciudadano(ciudadano: Ciudadano) -> tuple[int, bool]:
    """Guarda el registro y devuelve (id, nuevo). Lanza excepción si falla.

    Hay UN registro por número de WhatsApp: si la persona se registra de nuevo
    (corrige un dato), se actualiza su fila en vez de crear otra. Sin teléfono (CLI)
    siempre inserta.
    """
    valores = (ciudadano.telefono, ciudadano.cedula, ciudadano.nombres, ciudadano.correo,
               ciudadano.barrio, ciudadano.nombre_perfil, ciudadano.canal)
    columnas = sql.SQL("(telefono, cedula, nombres, correo, barrio, nombre_perfil, canal)")
    if ciudadano.telefono:
        consulta = sql.SQL(
            "insert into {} {} values (%s, %s, %s, %s, %s, %s, %s) "
            "on conflict (telefono) where telefono is not null do update set "
            "cedula = excluded.cedula, nombres = excluded.nombres, correo = excluded.correo, "
            "barrio = excluded.barrio, nombre_perfil = excluded.nombre_perfil, "
            "actualizado_en = now() "
            "returning id, (xmax = 0) as nuevo"
        ).format(_tabla("ciudadanos"), columnas)
    else:
        consulta = sql.SQL(
            "insert into {} {} values (%s, %s, %s, %s, %s, %s, %s) returning id, true"
        ).format(_tabla("ciudadanos"), columnas)
    with _get_pool().connection() as conn:
        fila = conn.execute(consulta, valores).fetchone()
    return int(fila[0]), bool(fila[1])


def buscar_ciudadano(telefono: str | None) -> dict | None:
    """Registro guardado para este número de WhatsApp, o None si no hay."""
    if not telefono:
        return None
    consulta = sql.SQL(
        "select cedula, nombres, correo, barrio from {} where telefono = %s"
    ).format(_tabla("ciudadanos"))
    with _get_pool().connection() as conn:
        fila = conn.execute(consulta, (telefono,)).fetchone()
    if fila is None:
        return None
    return {"cedula": fila[0], "nombres": fila[1], "correo": fila[2], "barrio": fila[3]}


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
