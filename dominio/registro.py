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


def nombre_de_pila(nombres: str | None) -> str | None:
    """Primer nombre para saludar, a partir del nombre del registro civil.

    La DINARDAP entrega "APELLIDO APELLIDO NOMBRE(S)". Con 3 o más palabras se toma
    la tercera; con menos no se puede separar con criterio y se usa la primera.
    """
    partes = (nombres or "").split()
    if not partes:
        return None
    return (partes[2] if len(partes) >= 3 else partes[0]).capitalize()


def nombre_para_mostrar(nombres: str | None) -> str | None:
    """'MORA PEREZ ANA LUCIA' -> 'Mora Perez Ana Lucia' (mismo orden de la fuente)."""
    return " ".join(p.capitalize() for p in (nombres or "").split()) or None
