"""
Términos y aviso de privacidad (F02 del flujo v2).
==================================================

El documento del cliente trae el texto como `{{enlace_privacidad}}` y
`{{finalidad_aprobada}}`, y dice: "Sin texto y finalidad aprobados, la captura queda
sin habilitar". Por eso vienen de variables de entorno y, si falta cualquiera de
las dos, el bot NO registra a nadie (la tool `aceptar_terminos` lo rechaza).

`PRIVACIDAD_VERSION` se guarda junto a cada aceptación: si mañana cambia el texto,
se sube la versión y queda claro qué aceptó cada persona.
"""

import os

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

PRIVACIDAD_ENLACE = os.getenv("PRIVACIDAD_ENLACE", "").strip()
PRIVACIDAD_FINALIDAD = os.getenv("PRIVACIDAD_FINALIDAD", "").strip()
PRIVACIDAD_VERSION = os.getenv("PRIVACIDAD_VERSION", "v1").strip() or "v1"


def esta_configurado() -> bool:
    return bool(PRIVACIDAD_ENLACE and PRIVACIDAD_FINALIDAD)
