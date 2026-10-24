"""
Tools del registro inicial (flujo v2 del cliente, F02 a F07).
=============================================================

Fuente: `Flujo_Agente_Juan_Jose.docx` (2026-10-24). Reemplaza al registro del guion
v1, donde la persona escribía su nombre y el correo era opcional. Ahora:

    F02 aceptar_terminos        términos y privacidad, OBLIGATORIO antes de pedir datos
    F03 consultar_cedula        cédula -> DINARDAP -> nombres completos
    F04 confirmar_nombres       "¿Son correctos?"  (no hay vía de nombre manual)
    F05 guardar_datos_contacto  correo, teléfono de contacto y barrio (todos obligatorios)
    F06 confirmar_perfil        resumen -> Confirmar datos / Cancelar registro
    F07                         menú principal, solo con el registro confirmado

Cada tool devuelve `siguiente_paso` (de `dominio/registro.py`): el modelo no tiene
que deducir el orden del flujo, se lo dice el backend.

REGLAS QUE SE HACEN CUMPLIR AQUÍ (no en el prompt)
- Sin términos aceptados no se guarda ningún dato (LOPDP).
- Los nombres SOLO salen de la DINARDAP. Si la consulta falla, no se registra: se
  ofrece reintentar o salir (criterio del documento; el nombre manual "no está
  aprobado"). La consulta la hace el backend, nunca el modelo.
- El teléfono de WhatsApp lo pone el canal en el contexto, nunca el modelo. El
  teléfono de CONTACTO es otro dato y lo da la persona (o pide usar el mismo).
- Cambiar un dato del perfil deshace su confirmación: hay que revisar el resumen
  otra vez (F06: "Corregir ... muestra nuevamente el resumen").
- La cédula no aparece en ningún log ni vuelve al modelo.
"""

import logging
from datetime import datetime, timezone

from langchain.tools import ToolRuntime, tool

from agent_context import ConversacionContext
from dominio import registro as paso
from dominio import terminos
from dominio.correo import normalizar_email
from dominio.identidad import validar_cedula
from dominio.telefono import enmascarar_correo, enmascarar_telefono, normalizar_telefono
from integraciones import persona
from simpatizantes import repositorio

logger = logging.getLogger(__name__)

MAX_TEXTO = 160

# Errores que ve el modelo (el prompt dice qué responder a cada uno).
SIN_TELEFONO = "SIN_TELEFONO"
NO_SE_PUDO_GUARDAR = "NO_SE_PUDO_GUARDAR"
TERMINOS_NO_CONFIGURADOS = "TERMINOS_NO_CONFIGURADOS"
TERMINOS_PENDIENTES = "TERMINOS_PENDIENTES"
CEDULA_INVALIDA = "CEDULA_INVALIDA"
CEDULA_NO_EXISTE = "CEDULA_NO_EXISTE"
SERVICIO_NO_DISPONIBLE = "SERVICIO_NO_DISPONIBLE"
SIN_NOMBRES_PENDIENTES = "SIN_NOMBRES_PENDIENTES"
CORREO_INVALIDO = "CORREO_INVALIDO"
TELEFONO_INVALIDO = "TELEFONO_INVALIDO"
PERFIL_INCOMPLETO = "PERFIL_INCOMPLETO"


def _limpio(valor: str | None) -> str | None:
    texto = " ".join(str(valor or "").split())[:MAX_TEXTO]
    return texto or None


def resumen_perfil(registro: dict | None) -> dict:
    """Datos para el resumen de F06/F11, con correo y teléfono enmascarados."""
    r = registro or {}
    return {
        "nombre_completo": paso.nombre_para_mostrar(r.get("nombres")),
        "correo_enmascarado": enmascarar_correo(r.get("correo")),
        "telefono_enmascarado": enmascarar_telefono(r.get("telefono_contacto")),
        "barrio": r.get("barrio"),
    }


def _resultado(registro: dict | None, ok: bool = True, error: str | None = None,
               **extra) -> dict:
    siguiente = paso.siguiente_paso(registro)
    resultado = {"ok": ok, "error": error, "siguiente_paso": siguiente,
                 "nombre": paso.nombre_de_pila((registro or {}).get("nombres"))}
    if siguiente in (paso.REVISAR_PERFIL, paso.COMPLETO):
        resultado["resumen"] = resumen_perfil(registro)
    resultado.update(extra)
    return resultado


def _leer(telefono: str | None) -> tuple[dict | None, dict | None]:
    """(registro, error) — error es un resultado listo para devolver."""
    if not telefono or not repositorio.esta_configurado():
        return None, {"ok": False, "error": SIN_TELEFONO, "siguiente_paso": None}
    try:
        return repositorio.buscar_ciudadano(telefono) or {}, None
    except Exception:  # noqa: BLE001
        logger.exception("registro: no se pudo leer el registro")
        return None, {"ok": False, "error": NO_SE_PUDO_GUARDAR, "siguiente_paso": None}


def _guardar(telefono: str, registro: dict, **campos) -> dict:
    """Actualiza y devuelve el registro nuevo, o un resultado de error."""
    try:
        return repositorio.actualizar_ciudadano(telefono, **campos)
    except Exception:  # noqa: BLE001
        logger.exception("registro: falló el guardado")
        return {"_error": NO_SE_PUDO_GUARDAR, **registro}


# ---------------------------------------------------------------------------
# Lógica (testeable sin el LLM)
# ---------------------------------------------------------------------------
def responder_terminos(acepta: bool, telefono: str | None,
                       nombre_perfil: str | None = None, canal: str = "whatsapp") -> dict:
    """F02. Guarda la respuesta a los términos (también el "No acepto")."""
    if not terminos.esta_configurado():
        return {"ok": False, "error": TERMINOS_NO_CONFIGURADOS, "siguiente_paso": None}
    registro, error = _leer(telefono)
    if error:
        return error
    campos = {"terminos_aceptados": bool(acepta),
              "terminos_version": terminos.PRIVACIDAD_VERSION,
              "terminos_respondido_en": datetime.now(timezone.utc), "canal": canal}
    if nombre_perfil:
        campos["nombre_perfil"] = nombre_perfil
    nuevo = _guardar(telefono, registro, **campos)
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo, acepto=bool(acepta))


def verificar_cedula(cedula: str | None, telefono: str | None) -> dict:
    """F03. Valida la cédula, consulta la DINARDAP y guarda los nombres SIN confirmar.

    Con `cedula` vacía usa la que ya estaba guardada (registros del guion v1, que
    tienen cédula pero no nombres del registro civil).
    """
    registro, error = _leer(telefono)
    if error:
        return error
    if registro.get("terminos_aceptados") is not True:
        return _resultado(registro, ok=False, error=TERMINOS_PENDIENTES)

    cruda = _limpio(cedula) or registro.get("cedula")
    if not cruda:
        return _resultado(registro, ok=False, error=CEDULA_INVALIDA)
    validacion = validar_cedula(cruda)
    if not validacion.valida:
        logger.info("registro: cédula inválida (%s)", validacion.motivo)
        return _resultado(registro, ok=False, error=CEDULA_INVALIDA)

    if not persona.esta_activa():
        logger.warning("registro: PERSONA_FUENTE=ninguna, no se puede consultar la cédula")
        return _resultado(registro, ok=False, error=SERVICIO_NO_DISPONIBLE)
    try:
        respuesta = persona.consultar_persona(validacion.normalizada)
    except Exception:  # noqa: BLE001
        logger.exception("registro: falló la consulta al registro civil")
        return _resultado(registro, ok=False, error=SERVICIO_NO_DISPONIBLE)

    if not respuesta["ok"]:
        codigo = respuesta["error"]["code"]
        if codigo in ("PERSON_NOT_FOUND", "PERSON_DECEASED"):
            logger.info("registro: la cédula no existe en el registro civil")
            return _resultado(registro, ok=False, error=CEDULA_NO_EXISTE)
        logger.warning("registro: registro civil no disponible (%s, corr=%s)",
                       codigo, respuesta["meta"]["correlation_id"])
        return _resultado(registro, ok=False, error=SERVICIO_NO_DISPONIBLE)

    nuevo = _guardar(telefono, registro, cedula=validacion.normalizada,
                     nombres=respuesta["data"]["full_name"], cedula_verificada=True,
                     nombres_confirmados=False, registro_confirmado_en=None)
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo, nombre_completo=paso.nombre_para_mostrar(nuevo.get("nombres")))


def responder_nombres(correctos: bool, telefono: str | None) -> dict:
    """F04. Sí -> nombres confirmados. Revisar cédula -> se borran cédula y nombres."""
    registro, error = _leer(telefono)
    if error:
        return error
    if not registro.get("cedula") or not registro.get("nombres"):
        return _resultado(registro, ok=False, error=SIN_NOMBRES_PENDIENTES)
    if correctos:
        campos = {"nombres_confirmados": True}
    else:
        campos = {"cedula": None, "nombres": None, "cedula_verificada": None,
                  "nombres_confirmados": False, "registro_confirmado_en": None}
    nuevo = _guardar(telefono, registro, **campos)
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo)


def guardar_contacto(telefono: str | None, correo: str | None = None,
                     telefono_contacto: str | None = None, usar_whatsapp: bool = False,
                     barrio: str | None = None) -> dict:
    """F05. Guarda los datos de contacto válidos que lleguen; avisa de los inválidos."""
    registro, error = _leer(telefono)
    if error:
        return error
    if registro.get("terminos_aceptados") is not True:
        return _resultado(registro, ok=False, error=TERMINOS_PENDIENTES)

    campos, errores = {}, []
    if _limpio(correo):
        normalizado = normalizar_email(correo)
        if normalizado:
            campos["correo"] = normalizado
        else:
            errores.append(CORREO_INVALIDO)
    if usar_whatsapp:
        campos["telefono_contacto"] = telefono
    elif _limpio(telefono_contacto):
        normalizado = normalizar_telefono(telefono_contacto)
        if normalizado:
            campos["telefono_contacto"] = normalizado
        else:
            errores.append(TELEFONO_INVALIDO)
    if _limpio(barrio):
        campos["barrio"] = _limpio(barrio)

    cambios = {k: v for k, v in campos.items() if v != registro.get(k)}
    if not cambios:
        return _resultado(registro, ok=not errores, error=", ".join(errores) or None)
    cambios["registro_confirmado_en"] = None      # cambió el perfil: hay que revisarlo
    nuevo = _guardar(telefono, registro, **cambios)
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo, error=", ".join(errores) or None)


def responder_perfil(confirma: bool, telefono: str | None) -> dict:
    """F06. Confirmar datos -> registro completo. Cancelar registro -> se borra la fila."""
    registro, error = _leer(telefono)
    if error:
        return error
    if not confirma:
        try:
            repositorio.borrar_ciudadano(telefono)
        except Exception:  # noqa: BLE001
            logger.exception("registro: no se pudo cancelar el registro")
            return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
        return {"ok": True, "error": None, "siguiente_paso": paso.TERMINOS,
                "registro_cancelado": True}
    if not paso.perfil_completo(registro):
        return _resultado(registro, ok=False, error=PERFIL_INCOMPLETO)
    if registro.get("registro_confirmado_en"):
        return _resultado(registro)
    nuevo = _guardar(telefono, registro, registro_confirmado_en=datetime.now(timezone.utc))
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo)


def _log(nombre: str, resultado: dict) -> dict:
    # Sin PII: solo el estado.
    logger.info("%s: ok=%s error=%s siguiente=%s", nombre, resultado.get("ok"),
                resultado.get("error"), resultado.get("siguiente_paso"))
    return resultado


def _ctx(runtime: ToolRuntime[ConversacionContext]) -> ConversacionContext:
    return runtime.context or ConversacionContext()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@tool
def aceptar_terminos(acepta: bool, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Guarda la respuesta de la persona a los términos y el aviso de privacidad (F02).

    Úsala solo cuando la respuesta sea inequívoca: "Acepto" (acepta=true) o "No
    acepto" (acepta=false). Si es ambigua, vuelve a preguntar sin llamarla.

    Returns:
        ok=true -> sigue con `siguiente_paso`. error TERMINOS_NO_CONFIGURADOS -> el
        registro no está disponible todavía.
    """
    ctx = _ctx(runtime)
    return _log("aceptar_terminos", responder_terminos(
        acepta, ctx.telefono, nombre_perfil=ctx.nombre_perfil, canal=ctx.canal))


@tool
def consultar_cedula(cedula: str, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Consulta la cédula en el registro civil y devuelve los nombres asociados (F03).

    Args:
        cedula: Número de cédula ecuatoriana tal como lo escribió la persona. Vacío
            ("") solo si `<estado_del_registro>` dice que ya hay una cédula guardada
            pendiente de consultar.

    Returns:
        ok=true -> muestra `nombre_completo` y pregunta si es correcto (F04).
        error: CEDULA_INVALIDA, CEDULA_NO_EXISTE, SERVICIO_NO_DISPONIBLE,
        TERMINOS_PENDIENTES.
    """
    return _log("consultar_cedula", verificar_cedula(cedula, _ctx(runtime).telefono))


@tool
def confirmar_nombres(correctos: bool, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Guarda si los nombres recuperados de la cédula son correctos (F04).

    correctos=true con "Sí, continuar". correctos=false con "Revisar cédula": se
    descartan cédula y nombres y hay que pedir la cédula otra vez.
    """
    return _log("confirmar_nombres", responder_nombres(correctos, _ctx(runtime).telefono))


@tool
def guardar_datos_contacto(
    runtime: ToolRuntime[ConversacionContext],
    correo: str = "",
    telefono: str = "",
    usar_whatsapp: bool = False,
    barrio: str = "",
) -> dict:
    """Guarda correo, teléfono de contacto y/o barrio (F05). Todos opcionales.

    Úsala CADA VEZ que la persona dé uno o más de estos datos; manda solo los que dio,
    tal como los escribió. Lo ya guardado se conserva.

    Args:
        correo: Correo electrónico.
        telefono: Teléfono de contacto que la persona escribió.
        usar_whatsapp: true SOLO si la persona dice que su teléfono de contacto es el
            mismo número desde el que escribe. Nunca lo supongas.
        barrio: Barrio o sector desde donde escribe, con sus palabras.

    Returns:
        `siguiente_paso` dice qué pedir ahora. error CORREO_INVALIDO o
        TELEFONO_INVALIDO -> pide revisar solo ese dato.
    """
    return _log("guardar_datos_contacto", guardar_contacto(
        _ctx(runtime).telefono, correo=correo, telefono_contacto=telefono,
        usar_whatsapp=usar_whatsapp, barrio=barrio))


@tool
def confirmar_perfil(confirma: bool, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Confirma o cancela el registro después del resumen de datos (F06).

    confirma=true con "Confirmar datos". confirma=false SOLO con "Cancelar registro":
    borra los datos de la persona. Para "Corregir" no la llames: pide el dato y
    guárdalo con la tool que corresponda.
    """
    return _log("confirmar_perfil", responder_perfil(confirma, _ctx(runtime).telefono))
