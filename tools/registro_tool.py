"""
Tools del registro inicial (flujo v4 del cliente, "FLUJO PROPUESTA V4").
========================================================================

Fuente: `TOTOBOT (1).docx` (2026-11-05). Reemplaza al flujo v2: ya no se confirman
los nombres ni se revisa un resumen. Ahora:

    consultar_cedula        cédula -> DINARDAP -> nombres ("¡Gracias, {nombre}!")
    guardar_datos_contacto  correo, celular y barrio -> con el barrio queda registrado

La bienvenida avisa que, al ingresar los datos, la persona acepta que se use su
información para contactarla (consentimiento del documento v4).

Cada tool devuelve `siguiente_paso` (de `dominio/registro.py`): el modelo no tiene
que deducir el orden del flujo, se lo dice el backend.

REGLAS QUE SE HACEN CUMPLIR AQUÍ (no en el prompt)
- Los nombres SOLO salen de la DINARDAP. Si la consulta falla, no se registra: se
  ofrece reintentar o salir. La consulta la hace el backend, nunca el modelo.
- Celular: 10 dígitos que empiezan con 09 (también se acepta escrito con +593).
- Barrio: con al menos dos letras (ni vacío, ni solo números o símbolos).
- El teléfono de WhatsApp lo pone el canal en el contexto, nunca el modelo. El
  celular de CONTACTO es otro dato y lo da la persona (o pide usar el mismo).
- Cuando el registro se completa se marcan `nombres_confirmados` y
  `registro_confirmado_en`, para que el equipo sepa desde cuándo está registrado.
- La cédula no aparece en ningún log ni vuelve al modelo.
"""

import logging
import re
from datetime import datetime, timezone

from langchain.tools import ToolRuntime, tool

from agent_context import ConversacionContext
from dominio import registro as paso
from dominio.correo import normalizar_email
from dominio.identidad import validar_cedula
from dominio.telefono import enmascarar_correo, enmascarar_telefono, normalizar_celular
from integraciones import persona
from simpatizantes import repositorio

logger = logging.getLogger(__name__)

MAX_TEXTO = 160

# Errores que ve el modelo (el prompt dice qué responder a cada uno).
SIN_TELEFONO = "SIN_TELEFONO"
NO_SE_PUDO_GUARDAR = "NO_SE_PUDO_GUARDAR"
CEDULA_INVALIDA = "CEDULA_INVALIDA"
CEDULA_NO_EXISTE = "CEDULA_NO_EXISTE"
SERVICIO_NO_DISPONIBLE = "SERVICIO_NO_DISPONIBLE"
CORREO_INVALIDO = "CORREO_INVALIDO"
TELEFONO_INVALIDO = "TELEFONO_INVALIDO"
BARRIO_INVALIDO = "BARRIO_INVALIDO"

# Un barrio tiene que traer al menos dos letras: "Guasmo Sur" sí, "123" o "..." no.
_LETRAS = re.compile(r"[^\W\d_]")


def _limpio(valor: str | None) -> str | None:
    texto = " ".join(str(valor or "").split())[:MAX_TEXTO]
    return texto or None


def resumen_perfil(registro: dict | None) -> dict:
    """Datos del registro con correo y teléfono enmascarados (lo que ve el modelo)."""
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
    if siguiente == paso.COMPLETO:
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
def verificar_cedula(cedula: str | None, telefono: str | None,
                     nombre_perfil: str | None = None, canal: str = "whatsapp") -> dict:
    """Valida la cédula, consulta la DINARDAP y guarda los nombres.

    Con `cedula` vacía usa la que ya estaba guardada (registros del guion v1, que
    tienen cédula pero no nombres del registro civil).
    """
    registro, error = _leer(telefono)
    if error:
        return error

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

    campos = {"cedula": validacion.normalizada, "nombres": respuesta["data"]["full_name"],
              "cedula_verificada": True, "nombres_confirmados": True, "canal": canal}
    if nombre_perfil:
        campos["nombre_perfil"] = nombre_perfil
    nuevo = _guardar(telefono, registro, **campos)
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo, nombre_completo=paso.nombre_para_mostrar(nuevo.get("nombres")))


def guardar_contacto(telefono: str | None, correo: str | None = None,
                     telefono_contacto: str | None = None, usar_whatsapp: bool = False,
                     barrio: str | None = None) -> dict:
    """Guarda los datos de contacto válidos que lleguen; avisa de los inválidos.

    Si con lo guardado el registro queda completo, marca `registro_confirmado_en`.
    """
    registro, error = _leer(telefono)
    if error:
        return error

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
        normalizado = normalizar_celular(telefono_contacto)
        if normalizado:
            campos["telefono_contacto"] = normalizado
        else:
            errores.append(TELEFONO_INVALIDO)
    if _limpio(barrio):
        if len(_LETRAS.findall(barrio)) >= 2:
            campos["barrio"] = _limpio(barrio)
        else:
            errores.append(BARRIO_INVALIDO)

    cambios = {k: v for k, v in campos.items() if v != registro.get(k)}
    if not cambios:
        return _resultado(registro, ok=not errores, error=", ".join(errores) or None)
    if (paso.siguiente_paso({**registro, **cambios}) == paso.COMPLETO
            and not registro.get("registro_confirmado_en")):
        cambios["registro_confirmado_en"] = datetime.now(timezone.utc)
    nuevo = _guardar(telefono, registro, **cambios)
    if "_error" in nuevo:
        return _resultado(registro, ok=False, error=NO_SE_PUDO_GUARDAR)
    return _resultado(nuevo, error=", ".join(errores) or None)


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
def consultar_cedula(cedula: str, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Consulta la cédula en el registro civil y guarda los nombres asociados.

    Args:
        cedula: Número de cédula ecuatoriana tal como lo escribió la persona. Vacío
            ("") solo si `<estado_del_registro>` dice que ya hay una cédula guardada
            pendiente de consultar.

    Returns:
        ok=true -> agradece con `nombre` y pide el correo (no hay que confirmar
        los nombres). error: CEDULA_INVALIDA, CEDULA_NO_EXISTE, SERVICIO_NO_DISPONIBLE.
    """
    ctx = _ctx(runtime)
    return _log("consultar_cedula", verificar_cedula(
        cedula, ctx.telefono, nombre_perfil=ctx.nombre_perfil, canal=ctx.canal))


@tool
def guardar_datos_contacto(
    runtime: ToolRuntime[ConversacionContext],
    correo: str = "",
    telefono: str = "",
    usar_whatsapp: bool = False,
    barrio: str = "",
) -> dict:
    """Guarda correo, celular de contacto y/o barrio. Todos opcionales.

    Úsala CADA VEZ que la persona dé uno o más de estos datos; manda solo los que dio,
    tal como los escribió. Lo ya guardado se conserva.

    Args:
        correo: Correo electrónico.
        telefono: Celular de contacto que la persona escribió.
        usar_whatsapp: true SOLO si la persona dice que su celular de contacto es el
            mismo número desde el que escribe. Nunca lo supongas.
        barrio: Barrio o sector desde donde escribe, con sus palabras.

    Returns:
        `siguiente_paso` dice qué pedir ahora; COMPLETO -> quedó registrado.
        error CORREO_INVALIDO, TELEFONO_INVALIDO o BARRIO_INVALIDO -> pide revisar
        solo ese dato.
    """
    return _log("guardar_datos_contacto", guardar_contacto(
        _ctx(runtime).telefono, correo=correo, telefono_contacto=telefono,
        usar_whatsapp=usar_whatsapp, barrio=barrio))
