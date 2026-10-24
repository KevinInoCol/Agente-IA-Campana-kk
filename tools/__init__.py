"""Tools del agente, agrupadas por flujo: registro inicial y "Quiero sumarme"."""

from .colaboracion_tool import confirmar_colaboracion, preparar_colaboracion
from .registro_tool import (
    aceptar_terminos,
    confirmar_nombres,
    confirmar_perfil,
    consultar_cedula,
    guardar_datos_contacto,
)

__all__ = [
    "aceptar_terminos", "consultar_cedula", "confirmar_nombres", "guardar_datos_contacto",
    "confirmar_perfil", "preparar_colaboracion", "confirmar_colaboracion",
]
