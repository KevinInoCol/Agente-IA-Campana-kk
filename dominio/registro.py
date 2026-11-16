"""
Paso pendiente del registro inicial (flujo v4) — dominio PURO.
=========================================================================

Con la fila de `ciudadanos` de un número de WhatsApp, dice qué toca ahora. Lo usan
las tools (para devolverle al modelo el `siguiente_paso`) y el middleware (para
contarle el estado en cada turno). Una sola implementación: el orden del flujo vive
aquí y en ningún otro lado.

Orden de "FLUJO PROPUESTA V4" (TOTOBOT.docx, 2026-11-05): ya no se confirman los
nombres ni se revisa un resumen; con el barrio la persona queda registrada.
    CEDULA -> CORREO -> TELEFONO -> BARRIO -> COMPLETO

Las columnas `nombres_confirmados` y `registro_confirmado_en` se siguen llenando
(las tools las marcan solas) para que el equipo vea cuándo se completó el registro,
pero ya no deciden el paso. Quien quedó a medias en los pasos viejos de confirmar
nombres o revisar el resumen pasa directo al siguiente dato que le falte.
"""

CEDULA = "CEDULA"
CORREO = "CORREO"
TELEFONO = "TELEFONO"
BARRIO = "BARRIO"
COMPLETO = "COMPLETO"

# Datos de contacto, en el orden en que se piden (F05).
CONTACTO = ((CORREO, "correo"), (TELEFONO, "telefono_contacto"), (BARRIO, "barrio"))


def siguiente_paso(registro: dict | None) -> str:
    """El paso del alta que falta, o COMPLETO."""
    r = registro or {}
    if not r.get("cedula") or not r.get("nombres"):
        return CEDULA
    for paso, campo in CONTACTO:
        if not r.get(campo):
            return paso
    return COMPLETO


def perfil_completo(registro: dict | None) -> bool:
    """True si están todos los datos del registro."""
    return siguiente_paso(registro) == COMPLETO


# Partículas de apellidos compuestos: van pegadas a la palabra que sigue, así
# "DI LORENZO" o "DE LA TORRE" cuentan como UN apellido. Sin esto, "PARRA DI LORENZO
# AMIRA" saludaba a "Lorenzo" (reporte de Giovani, 2026-11-16).
_PARTICULAS = frozenset({
    "DE", "DEL", "LA", "LAS", "LOS", "DI", "DA", "DAL", "DALLA", "DELLA", "DELLE",
    "DOS", "DAS", "DU", "VAN", "VON", "DER", "DEN", "TEN", "TER", "SAN", "SANTA", "MAC",
})


def _unidades(nombres: str | None) -> list[str]:
    """Agrupa las palabras: cada partícula se une a la siguiente palabra."""
    unidades: list[str] = []
    pendiente: list[str] = []
    for palabra in (nombres or "").split():
        pendiente.append(palabra)
        if palabra.upper() not in _PARTICULAS:
            unidades.append(" ".join(pendiente))
            pendiente = []
    if pendiente:  # partícula suelta al final: se pega a la última unidad
        if unidades:
            unidades[-1] += " " + " ".join(pendiente)
        else:
            unidades.append(" ".join(pendiente))
    return unidades


def separar_nombres(nombres: str | None) -> tuple[str, str]:
    """"APELLIDOS NOMBRES" de la DINARDAP -> (apellidos, nombres).

    Asume DOS apellidos (lo normal en Ecuador), contando los compuestos como uno:
        "PARRA DI LORENZO AMIRA"         -> ("PARRA DI LORENZO", "AMIRA")
        "DE LA TORRE MORA ANA LUCIA"     -> ("DE LA TORRE MORA", "ANA LUCIA")
    Con menos de 3 unidades no se puede separar con criterio: devuelve ("", todo).
    Sigue siendo una heurística: con UN solo apellido ("MORA ANA LUCIA") falla.
    """
    u = _unidades(nombres)
    if len(u) >= 3:
        return " ".join(u[:2]), " ".join(u[2:])
    return "", " ".join(u)


def nombre_de_pila(nombres: str | None) -> str | None:
    """Primer nombre para saludar, a partir del nombre del registro civil.

    "PARRA DI LORENZO AMIRA" -> "Amira"; "MORA PEREZ ANA LUCIA" -> "Ana". Con menos de
    3 unidades se usa la primera (comportamiento previo).
    """
    u = _unidades(nombres)
    if not u:
        return None
    elegido = u[2] if len(u) >= 3 else u[0]
    return " ".join(p.capitalize() for p in elegido.split())


def nombre_para_mostrar(nombres: str | None) -> str | None:
    """'MORA PEREZ ANA LUCIA' -> 'Mora Perez Ana Lucia' (mismo orden de la fuente)."""
    return " ".join(p.capitalize() for p in (nombres or "").split()) or None
