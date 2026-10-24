"""
Paso pendiente del registro inicial (flujo v2, F02 a F07) — dominio PURO.
=========================================================================

Con la fila de `ciudadanos` de un número de WhatsApp, dice qué toca ahora. Lo usan
las tools (para devolverle al modelo el `siguiente_paso`) y el middleware (para
contarle el estado en cada turno). Una sola implementación: el orden del flujo vive
aquí y en ningún otro lado.

Orden del documento del cliente:
    TERMINOS -> CEDULA -> CONFIRMAR_NOMBRES -> CORREO -> TELEFONO -> BARRIO
             -> REVISAR_PERFIL -> COMPLETO
"""

TERMINOS = "TERMINOS"
CEDULA = "CEDULA"
CONFIRMAR_NOMBRES = "CONFIRMAR_NOMBRES"
CORREO = "CORREO"
TELEFONO = "TELEFONO"
BARRIO = "BARRIO"
REVISAR_PERFIL = "REVISAR_PERFIL"
COMPLETO = "COMPLETO"

# Datos de contacto, en el orden en que se piden (F05).
CONTACTO = ((CORREO, "correo"), (TELEFONO, "telefono_contacto"), (BARRIO, "barrio"))


def siguiente_paso(registro: dict | None) -> str:
    """El paso del alta que falta, o COMPLETO."""
    r = registro or {}
    if r.get("terminos_aceptados") is not True:
        return TERMINOS
    if not r.get("cedula") or not r.get("nombres"):
        return CEDULA
    if not r.get("nombres_confirmados"):
        return CONFIRMAR_NOMBRES
    for paso, campo in CONTACTO:
        if not r.get(campo):
            return paso
    if not r.get("registro_confirmado_en"):
        return REVISAR_PERFIL
    return COMPLETO


def perfil_completo(registro: dict | None) -> bool:
    """True si están todos los datos del perfil (aunque falte confirmarlo en F06)."""
    return siguiente_paso(registro) in (REVISAR_PERFIL, COMPLETO)


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
