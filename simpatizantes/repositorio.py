"""
Repositorio de ciudadanos y simpatizantes en PostgreSQL.
========================================================

Único lugar que sabe cómo se guardan en la base:
  - `ciudadanos`: el registro inicial (cédula, nombres de la DINARDAP,
    correo, teléfono de contacto, barrio), uno por número de WhatsApp. Lo escriben
    las tools de `tools/registro_tool.py`.
  - `simpatizantes`: las colaboraciones de "Quiero sumarme" (una por idea), con
    estado borrador -> confirmada | cancelada. Las escriben las tools de
    `tools/colaboracion_tool.py`.
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


# Columnas de `ciudadanos` que las tools pueden escribir (lista blanca para el SQL
# dinámico de `actualizar_ciudadano`).
COLUMNAS_CIUDADANO = (
    "cedula", "nombres", "correo", "barrio", "telefono_contacto", "nombre_perfil",
    "canal", "cedula_verificada", "nombres_confirmados", "registro_confirmado_en",
)
# Columnas que se le devuelven al resto del código.
COLUMNAS_LECTURA = (
    "cedula", "nombres", "correo", "barrio", "telefono_contacto", "cedula_verificada",
    "nombres_confirmados", "registro_confirmado_en",
)


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


# ---------------------------------------------------------------------------
# Ciudadanos (registro inicial)
# ---------------------------------------------------------------------------
def buscar_ciudadano(telefono: str | None) -> dict | None:
    """Registro guardado para este número de WhatsApp, o None si no hay."""
    if not telefono:
        return None
    consulta = sql.SQL("select {} from {} where telefono = %s").format(
        sql.SQL(", ").join(map(sql.Identifier, COLUMNAS_LECTURA)), _tabla("ciudadanos"))
    with _get_pool().connection() as conn:
        fila = conn.execute(consulta, (telefono,)).fetchone()
    if fila is None:
        return None
    return dict(zip(COLUMNAS_LECTURA, fila))


def actualizar_ciudadano(telefono: str, **campos) -> dict:
    """Crea o actualiza la fila de este número con SOLO los campos dados.

    Lo que no se pasa se conserva. Devuelve el registro como queda. Lanza si falla.
    """
    desconocidas = set(campos) - set(COLUMNAS_CIUDADANO)
    if desconocidas:
        raise ValueError(f"Columnas no permitidas: {sorted(desconocidas)}")
    columnas = ["telefono", *campos]
    asignaciones = [sql.SQL("{} = excluded.{}").format(sql.Identifier(c), sql.Identifier(c))
                    for c in campos]
    asignaciones.append(sql.SQL("actualizado_en = now()"))
    consulta = sql.SQL(
        "insert into {} ({}) values ({}) "
        "on conflict (telefono) where telefono is not null do update set {}"
    ).format(
        _tabla("ciudadanos"),
        sql.SQL(", ").join(map(sql.Identifier, columnas)),
        sql.SQL(", ").join(sql.Placeholder() * len(columnas)),
        sql.SQL(", ").join(asignaciones),
    )
    with _get_pool().connection() as conn:
        conn.execute(consulta, (telefono, *campos.values()))
    return buscar_ciudadano(telefono) or {}


def borrar_ciudadano(telefono: str) -> None:
    """Elimina el registro de este número ("Cancelar registro" en F06)."""
    consulta = sql.SQL("delete from {} where telefono = %s").format(_tabla("ciudadanos"))
    with _get_pool().connection() as conn:
        conn.execute(consulta, (telefono,))


# ---------------------------------------------------------------------------
# Colaboraciones ("Quiero sumarme")
# ---------------------------------------------------------------------------
def obtener_borrador(telefono: str) -> dict | None:
    """La colaboración en borrador de este número, o None."""
    consulta = sql.SQL(
        "select id, ayuda from {} where telefono = %s and estado = 'borrador'"
    ).format(_tabla())
    with _get_pool().connection() as conn:
        fila = conn.execute(consulta, (telefono,)).fetchone()
    return {"id": fila[0], "ayuda": fila[1]} if fila else None


def guardar_borrador(telefono: str, ayuda: str, nombre: str, barrio: str,
                     nombre_perfil: str | None, canal: str) -> int:
    """Crea o reemplaza el borrador de este número. Devuelve su id."""
    consulta = sql.SQL(
        "insert into {} (telefono, nombre, barrio, ayuda, nombre_perfil, canal, estado) "
        "values (%s, %s, %s, %s, %s, %s, 'borrador') "
        "on conflict (telefono) where estado = 'borrador' do update set "
        "ayuda = excluded.ayuda, nombre = excluded.nombre, barrio = excluded.barrio, "
        "actualizado_en = now() returning id"
    ).format(_tabla())
    with _get_pool().connection() as conn:
        fila = conn.execute(
            consulta, (telefono, nombre, barrio, ayuda, nombre_perfil, canal)).fetchone()
    return int(fila[0])


def confirmar_borrador(telefono: str, nombre: str, barrio: str, correo: str,
                       telefono_contacto: str) -> int | None:
    """Pasa el borrador a 'confirmada' con los datos revisados. None si no había.

    Es UNA sentencia condicionada a `estado = 'borrador'`: repetirla no crea otra
    colaboración (F12: "Repetir Confirmar registro no debe crear otra").
    """
    consulta = sql.SQL(
        "update {} set estado = 'confirmada', confirmada_en = now(), actualizado_en = now(), "
        "nombre = %s, barrio = %s, correo = %s, telefono_contacto = %s "
        "where telefono = %s and estado = 'borrador' returning id"
    ).format(_tabla())
    with _get_pool().connection() as conn:
        fila = conn.execute(
            consulta, (nombre, barrio, correo, telefono_contacto, telefono)).fetchone()
    return int(fila[0]) if fila else None


def cancelar_borrador(telefono: str) -> bool:
    """Marca el borrador como 'cancelada'. True si había uno."""
    consulta = sql.SQL(
        "update {} set estado = 'cancelada', actualizado_en = now() "
        "where telefono = %s and estado = 'borrador' returning id"
    ).format(_tabla())
    with _get_pool().connection() as conn:
        return conn.execute(consulta, (telefono,)).fetchone() is not None


def confirmada_reciente(telefono: str, minutos: int) -> int | None:
    """Id de una colaboración confirmada en los últimos `minutos`, o None.

    Sirve para "Comprobar estado": si el borrador ya no está pero hay una confirmada
    hace un momento, el guardado anterior SÍ se hizo.
    """
    consulta = sql.SQL(
        "select id from {} where telefono = %s and estado = 'confirmada' "
        "and confirmada_en > now() - make_interval(mins => %s) "
        "order by confirmada_en desc limit 1"
    ).format(_tabla())
    with _get_pool().connection() as conn:
        fila = conn.execute(consulta, (telefono, minutos)).fetchone()
    return int(fila[0]) if fila else None


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
