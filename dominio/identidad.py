"""
Identidad: normalización y validación de la cédula ecuatoriana (dominio PURO).
=============================================================================

Copiado de MAD (`Backend/dominio/identidad.py`), donde valida el registro de ~37 mil
ciudadanos en producción. Orden de comprobaciones:

    1. 10 dígitos exactos tras normalizar
    2. provincia (2 primeros dígitos) entre 01 y 24, o 30
    3. tercer dígito < 6 (persona natural)
    4. dígito verificador por módulo 10

Aquí lo usa la tool `registrar_ciudadano` (registro de la bienvenida, guion v1 del
cliente). El `motivo` del resultado es interno: a la persona solo se le dice que
revise el número.

Módulo PURO: sin red, sin base de datos, sin framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Coeficientes del módulo 10 para los 9 primeros dígitos.
_COEFICIENTES = (2, 1, 2, 1, 2, 1, 2, 1, 2)

# Lo que la gente escribe dentro del número: "171-003-4065", "171 003 4065", "171.003…".
_SEPARADORES = re.compile(r"[\s.\-]")

# Provincias válidas: 01-24 y 30 (emitidas en el exterior).
_PROVINCIAS_VALIDAS = set(range(1, 25)) | {30}


@dataclass(frozen=True)
class ResultadoCedula:
    """Veredicto de la validación. `motivo` es interno: NUNCA se le muestra al ciudadano.

    La spec pide que la validación sea invisible para la persona (se le responde
    T-ID-INVALID a secas); el motivo existe para los logs y para depurar.
    """

    valida: bool
    normalizada: str
    motivo: str = ""


def normalizar_cedula(texto: str | None) -> str:
    """Extrae la ristra de dígitos más larga del texto.

    "mi cedula es 171 003 4065" -> "1710034065". Devuelve "" si no hay dígitos.
    Se toma la más larga, no la primera, porque la gente escribe cosas como
    "cédula 2 veces: 1710034065".
    """
    limpio = _SEPARADORES.sub("", texto or "")
    runs = re.findall(r"\d+", limpio)
    return max(runs, key=len) if runs else ""


def validar_cedula(texto: str | None) -> ResultadoCedula:
    """Valida una cédula ecuatoriana siguiendo el orden del spec §6."""
    n = normalizar_cedula(texto)

    if not n:
        return ResultadoCedula(False, "", "sin dígitos")
    if len(n) != 10:
        return ResultadoCedula(False, n, f"{len(n)} dígitos en vez de 10")
    if int(n[:2]) not in _PROVINCIAS_VALIDAS:
        return ResultadoCedula(False, n, "código de provincia fuera de rango (01-24, 30)")
    if int(n[2]) >= 6:
        return ResultadoCedula(False, n, "tercer dígito >= 6 (no es persona natural)")

    total = 0
    for digito, coef in zip(n[:9], _COEFICIENTES):
        producto = int(digito) * coef
        total += producto - 9 if producto >= 10 else producto
    verificador = (10 - (total % 10)) % 10
    if verificador != int(n[9]):
        return ResultadoCedula(False, n, "dígito verificador incorrecto")

    return ResultadoCedula(True, n)


def cedula_valida(cedula: str) -> bool:
    """Atajo booleano. Mantiene la firma que ya usaban `middleware/` y las tools."""
    return validar_cedula(cedula).valida


# ---------------------------------------------------------------------------
# Búsqueda de cédulas dentro de un texto libre
# ---------------------------------------------------------------------------
# Candidato: 10 dígitos que pueden venir separados por espacios, puntos, guiones o
# un salto de línea. Diez grupos porque el debounce concatena los mensajes de una
# ráfaga con "\n" y la gente parte el número en dos mensajes.
_CANDIDATO_CEDULA = re.compile(r"(?<!\d)(?:\d[\s.\-]{0,2}){9}\d(?!\d)")


def encontrar_cedulas(texto: str | None) -> list[tuple[str, str]]:
    """Todas las cédulas VÁLIDAS de un texto: [(tal como se escribió, normalizada)].

    Existe para que el canal pueda ENMASCARARLAS antes de invocar al agente. Y usa
    la misma normalización que `validar_cedula`, que es el punto: mientras el canal
    buscaba `\\b\\d{10}\\b` y el dominio aceptaba separadores, había números que el
    dominio daba por válidos y el canal no enmascaraba — y esos entraban en claro
    al checkpoint de LangGraph.

    Devuelve TODAS, no la primera: "la mía es X y la de mi esposa es Y" tiene dos
    personas, y tapar solo una filtra la otra.

    >>> encontrar_cedulas("mi cedula es 0915450001")
    [('0915450001', '0915450001')]
    >>> encontrar_cedulas("091-545-0001")
    [('091-545-0001', '0915450001')]
    >>> encontrar_cedulas("hola, tengo 30 años")
    []
    """
    if not texto:
        return []
    hallazgos: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for m in _CANDIDATO_CEDULA.finditer(str(texto)):
        bruto = m.group(0)
        r = validar_cedula(bruto)
        if r.valida and bruto not in vistos:
            vistos.add(bruto)
            hallazgos.append((bruto, r.normalizada))
    return hallazgos


def enmascarar_cedulas(texto: str | None, marcador: str) -> tuple[str, list[str]]:
    """Sustituye TODAS las cédulas por `marcador`. Devuelve (texto, normalizadas).

    Se aplica en el canal antes de invocar al grafo: LangGraph persiste la entrada
    cruda en el checkpoint ANTES de cualquier middleware, así que redactar más
    tarde llega tarde (verificado).
    """
    if not texto:
        return texto or "", []
    salida = str(texto)
    normalizadas: list[str] = []
    for bruto, norm in encontrar_cedulas(texto):
        salida = salida.replace(bruto, marcador)
        normalizadas.append(norm)
    return salida, normalizadas
