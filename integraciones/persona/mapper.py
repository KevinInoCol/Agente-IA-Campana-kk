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


def mapear_ventanilla_dinardap(respuesta: dict, cedula: str, referencia: str) -> PersonaDTO:
    """Respuesta de `/services/api/dinardap/consulta/{id}` -> `PersonaDTO`."""
    if not isinstance(respuesta, dict) or not respuesta:
        raise PersonaNoEncontrada()

    if "numeroRuc" in respuesta and "sexo" not in respuesta:
        raise RespuestaIlegible(
            "la fuente devolvió un registro del SRI (RUC), no del registro civil"
        )

    nombre_completo = str(respuesta.get("nombre") or "").strip()
    if not nombre_completo:
        raise PersonaNoEncontrada()

    if any(str(respuesta.get(c) or "").strip() for c in _CAMPOS_DEFUNCION):
        raise PersonaFallecida()

    return PersonaDTO(
        national_id=str(respuesta.get("cedula") or cedula).strip(),
        first_name=_nombre_de_pila(nombre_completo),
        full_name=nombre_completo,
        raw_source_ref=referencia,
    )
