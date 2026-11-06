"""
Mapeo de la respuesta de la DINARDAP al `PersonaDTO` (copiado de MAD).
======================================================================

TODO EL MAPEO DE CAMPOS VIVE AQUÍ: si mañana la fuente cambia un nombre de campo, se
toca este archivo y ningún otro.

Fuente: ventanilla municipal (rmpplayas) `/services/api/dinardap/consulta/{cedula}`,
verificada en MAD el 2026-08-26. Devuelve el registro civil completo (cedula, nombre,
sexo, fechaNacimiento, estadoCivil, domicilio, padres, defunción...).

A DIFERENCIA DE MAD, Toto solo usa la consulta para CORROBORAR que la cédula existe.
No necesita sexo ni fecha de nacimiento (MAD los usa para asignar comunidades), así
que no se mapean: menos datos personales en memoria y en la base (LOPDP).

TRAMPAS VERIFICADAS EN MAD
1. Una cédula INEXISTENTE responde **HTTP 500**, no 404 (lo maneja el adaptador).
2. No hay campo `apellido`: todo viene en `nombre`, con los APELLIDOS PRIMERO
   ("GUARACA VACACELA NICOLAS").
3. Un RUC (13 dígitos) devuelve el registro del SRI, no el civil.
"""

from __future__ import annotations

from dataclasses import dataclass

# Campos de defunción: una cédula de una persona fallecida no se registra.
_CAMPOS_DEFUNCION = ("fechaDefuncion", "causaMuerte", "fechaInscripcionDefuncion")


class PersonaNoEncontrada(Exception):
    """La fuente respondió correctamente pero no hay persona para esa cédula."""


class PersonaFallecida(Exception):
    """El registro civil marca a la persona como fallecida."""


class RespuestaIlegible(Exception):
    """La respuesta no tiene la forma esperada: no se mapea a ciegas."""


@dataclass(frozen=True)
class PersonaDTO:
    """Lo ÚNICO que sale de esta integración.

    `raw_source_ref` es una REFERENCIA de auditoría, no el payload crudo.
    """

    national_id: str
    first_name: str
    full_name: str
    raw_source_ref: str


def _nombre_de_pila(nombre_completo: str) -> str:
    """Extrae el/los nombre(s) de un "APELLIDOS NOMBRES" ecuatoriano.

        "GUARACA VACACELA NICOLAS"        -> "NICOLAS"
        "SUAREZ COLIMBA LUIS ALEXANDER"   -> "LUIS ALEXANDER"

    Es una HEURÍSTICA (asume dos apellidos). Con 1-2 palabras devuelve el texto
    completo en vez de recortar mal.
    """
    partes = [p for p in (nombre_completo or "").split() if p]
    if len(partes) >= 3:
        return " ".join(partes[2:])
    return " ".join(partes)


# Formatos tolerados. El servicio de Daule (SIGERI, 2026-11-06) reemplazó al de
# rmpplayas y su forma exacta aún no está verificada: se aceptan las variantes
# habituales en vez de exigir el `nombre` plano del servicio anterior.
_CLAVES_ENVOLTORIO = ("data", "datos", "persona", "resultado", "result", "respuesta",
                      "response", "ciudadano", "registro")
_CLAVES_NOMBRE_COMPLETO = ("nombre", "nombrecompleto", "nombre_completo",
                           "nombrescompletos", "nombres_completos")
_CLAVES_CEDULA = ("cedula", "identificacion", "numeroidentificacion", "numero_identificacion")


def _minusculas(d: dict) -> dict:
    return {str(k).lower(): v for k, v in d.items()}


def _texto(d: dict, claves) -> str:
    for c in claves:
        v = d.get(c)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _desenvolver(respuesta, profundidad: int = 0):
    """Baja por listas y envoltorios (`data`, `persona`...) hasta el registro."""
    if profundidad > 3:
        return respuesta
    if isinstance(respuesta, list):
        return _desenvolver(respuesta[0], profundidad + 1) if respuesta else {}
    if not isinstance(respuesta, dict):
        return respuesta
    d = _minusculas(respuesta)
    if _texto(d, _CLAVES_NOMBRE_COMPLETO) or "nombres" in d:
        return respuesta
    for c in _CLAVES_ENVOLTORIO:
        if isinstance(d.get(c), (dict, list)):
            return _desenvolver(d[c], profundidad + 1)
    return respuesta


def forma(x):
    """Estructura de un JSON (claves y tipos), SIN valores: apta para logs (LOPDP)."""
    if isinstance(x, dict):
        return {k: forma(v) for k, v in x.items()}
    if isinstance(x, list):
        return [forma(x[0])] if x else []
    return type(x).__name__


def mapear_ventanilla_dinardap(respuesta, cedula: str, referencia: str) -> PersonaDTO:
    """Respuesta de la ventanilla DINARDAP -> `PersonaDTO`.

    Acepta el formato de rmpplayas (`nombre` = "APELLIDOS NOMBRES") y variantes:
    envuelto en `data`/`persona`/..., dentro de una lista, o con `apellidos` y
    `nombres` por separado.
    """
    respuesta = _desenvolver(respuesta)
    if not isinstance(respuesta, dict) or not respuesta:
        raise PersonaNoEncontrada()
    d = _minusculas(respuesta)

    if "numeroruc" in d and "sexo" not in d:
        raise RespuestaIlegible(
            "la fuente devolvió un registro del SRI (RUC), no del registro civil"
        )

    nombre_completo = _texto(d, _CLAVES_NOMBRE_COMPLETO)
    nombres = _texto(d, ("nombres",))
    apellidos = _texto(d, ("apellidos",))
    if nombre_completo:
        first_name = _nombre_de_pila(nombre_completo)
    elif nombres and apellidos:
        nombre_completo, first_name = f"{apellidos} {nombres}", nombres
    elif nombres:
        nombre_completo, first_name = nombres, _nombre_de_pila(nombres)
    else:
        raise PersonaNoEncontrada()

    if any(str(d.get(c.lower()) or "").strip() for c in _CAMPOS_DEFUNCION):
        raise PersonaFallecida()

    return PersonaDTO(
        national_id=_texto(d, _CLAVES_CEDULA) or cedula,
        first_name=first_name,
        full_name=nombre_completo,
        raw_source_ref=referencia,
    )
