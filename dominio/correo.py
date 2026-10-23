"""
Correo electrónico: normalización para poder hashear y deduplicar (dominio PURO).
=================================================================================

POR QUÉ EXISTE
--------------
Copiado de MAD (`Backend/dominio/correo.py`). Si el mismo correo llega escrito
distinto, sin normalizar parecería otro contacto:

    "Juan.Perez@Gmail.com "   ->  "juan.perez@gmail.com"

Módulo puro: sin red, sin base de datos, sin framework.

QUÉ SE NORMALIZA Y QUÉ NO
-------------------------
Se recorta el espacio y se pasa TODO a minúscula. Estrictamente, la parte local de
un correo es sensible a mayúsculas según el RFC 5321, pero en la práctica ningún
proveedor la trata así, y para deduplicar un padrón conviene que
`JUAN@gmail.com` y `juan@gmail.com` sean la misma persona.

Lo que NO se hace: quitar puntos ni sufijos `+algo` de Gmail. Eso es una regla de
UN proveedor, y aplicarla a todos uniría correos de personas distintas en dominios
que sí los distinguen.

VALIDACIÓN: A PROPÓSITO LAXA
----------------------------
Solo se exige la forma `algo@algo.algo` sin espacios. No se valida el dominio ni
se comprueba que exista: un correo que no existe es un dato inútil, pero un correo
RECHAZADO por una regex demasiado estricta es un dato PERDIDO, y eso es peor. La
verdad sobre si un correo funciona la da el primer envío, no una expresión regular.
"""

from __future__ import annotations

import re

# `algo@algo.algo`, sin espacios y con un punto en el dominio. Deliberadamente
# permisiva: ver el docstring.
_FORMA = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

LARGO_MAXIMO = 254          # RFC 5321: límite del camino de reenvío


def normalizar_email(valor: str | None) -> str | None:
    """Lleva un correo a su forma canónica, o devuelve None si no tiene forma de correo.

    Devolver None en vez de adivinar: guardar un contacto equivocado es peor que no
    guardar ninguno.

    >>> normalizar_email("  Juan.Perez@Gmail.COM ")
    'juan.perez@gmail.com'
    >>> normalizar_email("sin arroba") is None
    True
    >>> normalizar_email("dos@@arrobas.com") is None
    True
    >>> normalizar_email("falta@dominio") is None
    True
    >>> normalizar_email(None) is None
    True
    """
    if not valor:
        return None

    limpio = str(valor).strip().lower()
    if not limpio or len(limpio) > LARGO_MAXIMO:
        return None
    if not _FORMA.match(limpio):
        return None
    return limpio
