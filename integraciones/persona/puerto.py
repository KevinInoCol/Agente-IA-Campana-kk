"""
Puerto de la fuente de identidad (copiado de MAD, `integraciones/persona/puerto.py`).
====================================================================================

El registro depende de ESTA interfaz, no de un proveedor concreto: así se cambia de
fuente (stub, DINARDAP vía ventanilla) sin tocar la tool, y los tests nunca salen a
la red real.
"""

from __future__ import annotations

from typing import Protocol


class FuentePersona(Protocol):
    """Consulta los datos de identidad de una persona por su cédula."""

    nombre: str

    def consultar(self, cedula: str, correlation_id: str) -> dict:
        """Devuelve el envelope `{ok, data, error, meta}`.

        `cedula` llega YA validada y normalizada (10 dígitos). El adapter no valida:
        de eso se encarga `consultar_persona`, para que ningún adapter pueda saltarse
        la regla de "nunca consultar con una cédula inválida".
        """
        ...
