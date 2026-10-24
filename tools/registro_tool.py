"""
Tool: registrar al ciudadano (bienvenida del guion v1 del cliente).
===================================================================

El guion "FlujoTTRBOT v1" pide en la bienvenida cédula, nombres completos, correo y
barrio, para "unir a las personas a la campaña". El cliente aclaró que solo hay que
pedir los datos que FALTAN: si la persona ya está en la base, no se le vuelve a pedir
nada, y si le falta uno, solo ese.

Por eso todos los argumentos son opcionales y la tool COMBINA lo nuevo con lo que ya
hay guardado para este número de WhatsApp (el teléfono lo pone el canal, no el
modelo). Lo que el modelo no manda, se conserva.

CADA DATO SE GUARDA APENAS LLEGA, aunque el registro esté incompleto. La primera
versión esperaba a tener cédula, nombres y barrio para guardar, y en la prueba con el
modelo real se perdían datos: la persona daba nombre y barrio, luego la cédula, y el
modelo mandaba solo la cédula (como dice el docstring), así que terminaba pidiendo de
nuevo lo que ya le habían dicho. Sin teléfono (CLI) no hay dónde acumular, y ahí sí se
exige el registro completo de una vez.

Validación (backend, no el modelo):
  - cédula: ecuatoriana, provincia + módulo 10 (`dominio/identidad.py`, el mismo
    algoritmo que usa MAD en producción).
  - correo: forma `algo@algo.algo` (`dominio/correo.py`). Es OPCIONAL: si la persona
    no tiene o no quiere darlo, el registro igual se completa.
  - nombres y barrio: texto no vacío.

Cédula en el registro civil (DINARDAP, `integraciones/persona/`, el mismo adapter que
MAD): si `PERSONA_FUENTE` no es `ninguna`, una cédula válida se consulta ANTES de
guardarla. La consulta la hace el backend, no el modelo.
  - existe            -> se guarda con `cedula_verificada = true`.
  - no existe / fallecida -> NO se guarda; error CEDULA_NO_EXISTE (pide revisarla).
  - fuente caída o mal configurada -> se guarda igual con `cedula_verificada = false`.
    Un fallo del Registro Civil no puede dejar a la persona sin registrarse.
Si la cédula ya estaba guardada y verificada, no se vuelve a consultar.

Obligatorios para que el registro quede COMPLETO: cédula, nombres y barrio.
"""

import logging

from langchain.tools import ToolRuntime, tool

from agent_context import ConversacionContext
from dominio.correo import normalizar_email
from dominio.identidad import validar_cedula
from integraciones import persona
from simpatizantes import Ciudadano, repositorio

logger = logging.getLogger(__name__)

OBLIGATORIOS = ("cedula", "nombres", "barrio")
MAX_TEXTO = 160
NO_EXISTE = "NO_EXISTE"


def _limpio(valor: str | None) -> str | None:
    texto = " ".join(str(valor or "").split())[:MAX_TEXTO]
    return texto or None


def verificar_cedula(cedula: str) -> bool | str | None:
    """Consulta el registro civil. True existe, NO_EXISTE, False sin respuesta, None apagado."""
    if not persona.esta_activa():
        return None
    try:
        r = persona.consultar_persona(cedula)
    except Exception:  # noqa: BLE001
        logger.exception("registrar_ciudadano: falló la consulta al registro civil")
        return False
    if r["ok"]:
        return True
    codigo = r["error"]["code"]
    if codigo in ("PERSON_NOT_FOUND", "PERSON_DECEASED"):
        return NO_EXISTE
    # Sin la cédula en el log: solo el código y el correlation_id.
    logger.warning("registrar_ciudadano: registro civil no disponible (%s, corr=%s)",
                   codigo, r["meta"]["correlation_id"])
    return False


def primer_nombre(nombres: str | None) -> str | None:
    return nombres.split()[0].capitalize() if nombres else None


def registrar_datos(
    cedula: str | None = None,
    nombres: str | None = None,
    correo: str | None = None,
    barrio: str | None = None,
    telefono: str | None = None,
    nombre_perfil: str | None = None,
    canal: str = "whatsapp",
) -> dict:
    """Valida, combina con lo guardado y persiste. Testeable sin el LLM.

    Devuelve {"ok", "registro_completo", "nuevo", "nombre", "faltan", "error"}.
    `ok` dice si se GUARDÓ algo; `registro_completo`, si ya no falta ningún obligatorio.
    """
    if not repositorio.esta_configurado():
        return {"ok": False, "registro_completo": False, "faltan": [],
                "error": "No hay dónde guardar los datos."}

    errores = []
    datos = {"cedula": None, "nombres": _limpio(nombres), "correo": None,
             "barrio": _limpio(barrio)}

    if _limpio(cedula):
        resultado = validar_cedula(cedula)
        if resultado.valida:
            datos["cedula"] = resultado.normalizada
        else:
            logger.info("registrar_ciudadano: cédula inválida (%s)", resultado.motivo)
            errores.append("CEDULA_INVALIDA")

    if _limpio(correo):
        datos["correo"] = normalizar_email(correo)
        if datos["correo"] is None:
            errores.append("CORREO_INVALIDO")

    try:
        guardado = repositorio.buscar_ciudadano(telefono) or {}
    except Exception:  # noqa: BLE001
        logger.exception("registrar_ciudadano: no se pudo leer el registro previo")
        return {"ok": False, "registro_completo": False, "faltan": [],
                "error": "No se pudo guardar en este momento."}

    verificada = guardado.get("cedula_verificada")
    if datos["cedula"] and not (datos["cedula"] == guardado.get("cedula") and verificada):
        verificada = verificar_cedula(datos["cedula"])
        if verificada == NO_EXISTE:
            logger.info("registrar_ciudadano: la cédula no existe en el registro civil")
            errores.append("CEDULA_NO_EXISTE")
            datos["cedula"] = None
            verificada = guardado.get("cedula_verificada")

    combinado = {k: datos[k] or guardado.get(k) for k in datos}
    faltan = [k for k in OBLIGATORIOS if not combinado[k]]
    if not combinado["correo"] and "CORREO_INVALIDO" not in errores:
        faltan.append("correo (opcional)")

    completo = all(combinado[k] for k in OBLIGATORIOS)
    hay_algo_nuevo = any(datos[k] for k in datos)
    if not hay_algo_nuevo or (not telefono and not completo):
        # Nada válido que guardar (p. ej. solo una cédula inválida), o sin teléfono
        # donde acumular un registro parcial.
        return {"ok": False, "registro_completo": completo, "nuevo": False,
                "nombre": primer_nombre(combinado["nombres"]), "faltan": faltan,
                "error": ", ".join(errores) or None}

    try:
        _, nuevo = repositorio.guardar_ciudadano(Ciudadano(
            cedula=combinado["cedula"], nombres=combinado["nombres"],
            correo=combinado["correo"], barrio=combinado["barrio"],
            telefono=telefono, nombre_perfil=nombre_perfil, canal=canal,
            cedula_verificada=verificada,
        ))
    except Exception:  # noqa: BLE001
        logger.exception("registrar_ciudadano: falló el guardado")
        return {"ok": False, "registro_completo": False, "faltan": faltan,
                "error": "No se pudo guardar en este momento."}

    return {"ok": True, "registro_completo": completo, "nuevo": nuevo,
            "nombre": primer_nombre(combinado["nombres"]), "faltan": faltan,
            "error": ", ".join(errores) or None}


@tool
def registrar_ciudadano(
    runtime: ToolRuntime[ConversacionContext],
    cedula: str = "",
    nombres: str = "",
    correo: str = "",
    barrio: str = "",
) -> dict:
    """Registra (o completa el registro de) la persona en la campaña.

    Úsala CADA VEZ que la persona te dé uno o más de estos datos, aunque falten
    otros: cada dato se guarda apenas llega. Manda los que dio, tal como los escribió;
    los que ya estaban guardados se conservan. El número de WhatsApp lo agrega el
    sistema: no lo pidas.

    Args:
        cedula: Número de cédula ecuatoriana (10 dígitos).
        nombres: Nombres y apellidos completos.
        correo: Correo electrónico (opcional).
        barrio: Barrio o sector de Guayaquil donde vive.

    Returns:
        registro_completo=true -> ya está registrada; `nombre` es su primer nombre
        para saludarla. registro_completo=false -> pide SOLO lo que está en `faltan`.
        `error`: CEDULA_INVALIDA o CEDULA_NO_EXISTE (pide revisar el número) o
        CORREO_INVALIDO (pide revisar el correo). ok=false sin `faltan` -> no se pudo guardar.
    """
    ctx = runtime.context or ConversacionContext()
    resultado = registrar_datos(
        cedula=cedula, nombres=nombres, correo=correo, barrio=barrio,
        telefono=ctx.telefono, nombre_perfil=ctx.nombre_perfil, canal=ctx.canal,
    )
    # Log sin PII: solo el estado.
    logger.info("registrar_ciudadano: ok=%s faltan=%s error=%s",
                resultado["ok"], resultado.get("faltan"), resultado.get("error"))
    return resultado
