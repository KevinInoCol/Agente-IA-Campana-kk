"""
Configuración del DEBOUNCE de mensajes (Redis).
===============================================

Único lugar que lee las variables `REDIS_*` y `DEBOUNCE_*` del entorno.

NOMBRES DE VARIABLES — transición: este paquete se llamaba `message_buffer` y sus
variables `MESSAGE_BUFFER_*`. Se admiten AMBOS nombres (primero `DEBOUNCE_*`, y si no
está, el antiguo) y se avisa por consola cuando se usa el viejo. El motivo es concreto: si
en el panel de despliegue quedan las variables antiguas y el código solo leyera las
nuevas, el debounce se activaría con su default (True) ignorando un
`MESSAGE_BUFFER_ENABLED=false` — y sin credenciales del canal las respuestas se
perderían en silencio. Cuando el despliegue esté migrado, se puede quitar el respaldo.
Carga su propio `.env` (mismo patrón que `tools/conocimiento_tool.py` y
`observabilty/`: sus valores se leen a nivel de módulo y podrían importarse
antes de que otro módulo cargue el entorno).

El *debounce* espera `DEBOUNCE_WINDOW_SECONDS` de silencio (default 15s):
los mensajes que un mismo contacto envía dentro de esa ventana se acumulan en
Redis y se concatenan en UNA sola entrada para el agente -> UNA sola respuesta.
"""

import os

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())


def _bool(valor: str | None, default: bool = False) -> bool:
    """Interpreta un valor de entorno como booleano (tolerante a vacío/None)."""
    if valor is None or valor.strip() == "":
        return default
    return valor.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _env(sufijo: str) -> str | None:
    """Lee `DEBOUNCE_<sufijo>` y, si no está, el antiguo `MESSAGE_BUFFER_<sufijo>`.

    Avisa cuando se usa el nombre viejo, para que la migración del panel de
    despliegue sea visible en los logs y no una sorpresa.
    """
    valor_nuevo = os.getenv(f"DEBOUNCE_{sufijo}")
    if valor_nuevo is not None and valor_nuevo.strip() != "":
        return valor_nuevo
    nombre_viejo = "MESSAGE_BUFFER_" + sufijo          # nombre previo al renombrado
    valor_viejo = os.getenv(nombre_viejo)
    if valor_viejo is not None and valor_viejo.strip() != "":
        print(f"⚠️  Variable ANTIGUA en uso: {nombre_viejo}. Renombrala a "
              f"DEBOUNCE_{sufijo} (el paquete `message_buffer` pasó a llamarse `debounce`).")
        return valor_viejo
    return None


# ============================================
# 1. CONEXIÓN A REDIS
# ============================================
# Opción A: URL única (tiene PRIORIDAD si está definida). Usar rediss:// para TLS.
REDIS_URL = os.getenv("REDIS_URL", "").strip()
# Opción B: piezas granulares (se usan solo si REDIS_URL está vacío).
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
# REDIS_DB es el índice numérico (0-15) del DB lógico, NO el nombre que muestra
# el panel de Redis Cloud (en planes Free/Fixed normalmente solo existe el 0).
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
REDIS_TLS = _bool(os.getenv("REDIS_TLS"), False)

# ============================================
# 2. BUFFER / DEBOUNCE
# ============================================
# Interruptor maestro (DEBOUNCE_ENABLED): si es False, el canal responde mensaje a mensaje
# (modo antiguo, sin Redis) — útil como fallback si Redis no está disponible.
BUFFER_ENABLED = _bool(_env("ENABLED"), True)

# Ventana de silencio (segundos) antes de concatenar y responder. Default: 15.
BUFFER_WINDOW_SECONDS = float(_env("WINDOW_SECONDS") or "15")

# TTL de las claves del buffer en Redis; debe ser MAYOR que la ventana para que
# nunca expire un buffer en pleno debounce. Red de seguridad ante tareas huérfanas.
BUFFER_TTL_SECONDS = int(_env("TTL_SECONDS") or "300")

# Cómo se unen los mensajes concatenados. En el .env llega literal ("\n" ->
# barra + n), así que decodificamos las secuencias de escape unicode.
_sep_raw = _env("SEPARATOR") or "\n"
try:
    BUFFER_SEPARATOR = _sep_raw.encode("utf-8").decode("unicode_escape")
except (UnicodeDecodeError, ValueError):
    BUFFER_SEPARATOR = "\n"

# Prefijo (namespace) de las claves en Redis. Claves resultantes:
#   <prefijo>:buffer:msgs:{conversacion}   y   <prefijo>:buffer:seq:{conversacion}
#
# ⚠️ ESTE PREFIJO ES EL AISLAMIENTO ENTRE CAMPAÑAS. La instancia de Redis se COMPARTE
# entre proyectos, y en Redis Cloud (planes free/fixed) normalmente solo existe la db 0,
# así que no se puede separar por índice de DB: el prefijo es el único mecanismo real.
# El default histórico era "avanza" —el nombre de OTRA campaña—, con lo que dos campañas
# en la misma instancia escribían bajo el MISMO namespace. Si dos webhooks distintos
# resolvieran el mismo id de conversación (p. ej. un id de conversación de respaldo
# del canal), los mensajes de un ciudadano se concatenarían en la inferencia de la otra
# campaña.
#
# Se deriva de DB_SCHEMA —la identidad del tenant ya definida para Postgres, una sola
# fuente de verdad (skill `postgres-tenant-schema-naming`)— y se puede sobreescribir con
# DEBOUNCE_KEY_PREFIX. Si no hay ninguna de las dos, cae a un genérico y se avisa
# al arrancar (ver el lifespan de main_conecta.py).
# Identidad del tenant para las claves: el prefijo explícito o, si no, el esquema de
# Postgres (misma identidad, una sola fuente de verdad). Se lee UNA vez para no repetir
# el aviso de variable antigua.
_PREFIJO_TENANT = (_env("KEY_PREFIX") or "").strip() or os.getenv("DB_SCHEMA", "").strip()

REDIS_KEY_PREFIX = _PREFIJO_TENANT or "app"

# True si el prefijo NO identifica al tenant (no se configuró ni DEBOUNCE_KEY_PREFIX ni
# DB_SCHEMA). Con el debounce activo y Redis COMPARTIDO entre campañas, eso es un riesgo
# de cruce de claves: el canal lo avisa en el arranque.
KEY_PREFIX_GENERICO = not _PREFIJO_TENANT

