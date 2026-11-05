"""
Tools de "Quiero unirme" / "Quiero hacer una propuesta" (flujo v4 del cliente).
===============================================================================

La idea se guarda en dos tiempos (pasos 10 y 11 de TOTOBOT.docx):

    preparar_colaboracion   guarda la idea como BORRADOR ("¿Está correcta?")
    confirmar_colaboracion  "Está correcta, enviar al equipo" -> 'confirmada'
                            (confirma=false -> 'cancelada', sin uso en el guion v4)

La idea tiene que tener al menos MIN_AYUDA caracteres ("Cuéntame un poco más").

Los datos de contacto NO se vuelven a pedir: salen del registro inicial. Al
confirmar se guarda una copia de lo que la persona revisó en el resumen.

RESULTADO DEL GUARDADO (F12)
- Confirmar es UNA sentencia condicionada a `estado = 'borrador'`: repetirla no crea
  otra colaboración. Si el borrador ya no está pero hay una confirmada hace un
  momento, se responde que YA quedó registrada (sirve para "Comprobar estado").
- Si no se pudo conectar a la base, el guardado no ocurrió: FALLO (reintentar).
- Si falló en medio de la operación, no sabemos si quedó: INCIERTO. Volver a llamar
  a `confirmar_colaboracion` comprueba el estado sin duplicar.

Apps Script (opcional): si `APPS_SCRIPT_WEBHOOK_URL` está definida, se manda una
copia al confirmar. Un fallo ahí no cambia el resultado: la copia que manda es
Postgres.
"""

import logging
from datetime import datetime, timezone

from langchain.tools import ToolRuntime, tool
from psycopg_pool import PoolTimeout

from agent_context import ConversacionContext
from dominio import registro as paso
from simpatizantes import apps_script, repositorio
from tools.registro_tool import resumen_perfil

logger = logging.getLogger(__name__)

MAX_AYUDA = 2000
# Paso 10 del documento: "mensaje de menos de ~10 caracteres" -> pedir más detalle.
MIN_AYUDA = 10
# Ventana en la que una colaboración recién confirmada cuenta como "ya registrada".
MINUTOS_CONFIRMADA_RECIENTE = 30

SIN_TELEFONO = "SIN_TELEFONO"
PERFIL_INCOMPLETO = "PERFIL_INCOMPLETO"
SIN_IDEA = "SIN_IDEA"
IDEA_CORTA = "IDEA_CORTA"
SIN_BORRADOR = "SIN_BORRADOR"
FALLO = "FALLO"
RESULTADO_INCIERTO = "RESULTADO_INCIERTO"


def _perfil(telefono: str | None) -> tuple[dict | None, dict | None]:
    if not telefono or not repositorio.esta_configurado():
        return None, {"ok": False, "error": SIN_TELEFONO}
    try:
        registro = repositorio.buscar_ciudadano(telefono) or {}
    except Exception:  # noqa: BLE001
        logger.exception("colaboración: no se pudo leer el registro")
        return None, {"ok": False, "error": FALLO}
    if not paso.perfil_completo(registro):
        return None, {"ok": False, "error": PERFIL_INCOMPLETO,
                      "siguiente_paso": paso.siguiente_paso(registro)}
    return registro, None


def preparar(ayuda: str | None, telefono: str | None, nombre_perfil: str | None = None,
             canal: str = "whatsapp") -> dict:
    """Guarda (o reemplaza) el borrador y devuelve el texto para confirmarlo."""
    ayuda = " ".join((ayuda or "").split())[:MAX_AYUDA]
    if not ayuda:
        return {"ok": False, "error": SIN_IDEA}
    if len(ayuda) < MIN_AYUDA:
        return {"ok": False, "error": IDEA_CORTA}
    registro, error = _perfil(telefono)
    if error:
        return error
    try:
        repositorio.guardar_borrador(telefono, ayuda, registro["nombres"], registro["barrio"],
                                     nombre_perfil, canal)
    except Exception:  # noqa: BLE001
        logger.exception("colaboración: no se pudo guardar el borrador")
        return {"ok": False, "error": FALLO}
    return {"ok": True, "error": None,
            "resumen": {**resumen_perfil(registro), "ayuda": ayuda}}


def confirmar(confirma: bool, telefono: str | None) -> dict:
    """Confirma (idempotente) o cancela el borrador."""
    registro, error = _perfil(telefono)
    if error:
        return error
    nombre = paso.nombre_de_pila(registro.get("nombres"))

    if not confirma:
        try:
            repositorio.cancelar_borrador(telefono)
        except Exception:  # noqa: BLE001
            logger.exception("colaboración: no se pudo cancelar el borrador")
            return {"ok": False, "error": FALLO}
        return {"ok": True, "error": None, "cancelada": True}

    try:
        borrador = repositorio.obtener_borrador(telefono)
        if borrador is None:
            if repositorio.confirmada_reciente(telefono, MINUTOS_CONFIRMADA_RECIENTE):
                return {"ok": True, "error": None, "ya_registrada": True, "nombre": nombre}
            return {"ok": False, "error": SIN_BORRADOR}
    except PoolTimeout:
        logger.exception("colaboración: sin conexión a la base")
        return {"ok": False, "error": FALLO}
    except Exception:  # noqa: BLE001
        logger.exception("colaboración: no se pudo leer el borrador")
        return {"ok": False, "error": FALLO}

    try:
        id_ = repositorio.confirmar_borrador(telefono, registro["nombres"], registro["barrio"],
                                             registro["correo"], registro["telefono_contacto"])
    except PoolTimeout:
        # No llegó a ejecutarse: el guardado NO ocurrió.
        logger.exception("colaboración: sin conexión a la base al confirmar")
        return {"ok": False, "error": FALLO}
    except Exception:  # noqa: BLE001 — pudo quedar hecho o no
        logger.exception("colaboración: resultado incierto al confirmar")
        return {"ok": False, "error": RESULTADO_INCIERTO}
    if id_ is None:
        # Otro intento la confirmó entre la lectura y el update.
        return {"ok": True, "error": None, "ya_registrada": True, "nombre": nombre}

    # Registros completados antes del flujo v4 pueden no tener la marca: se pone aquí.
    if not registro.get("registro_confirmado_en"):
        try:
            repositorio.actualizar_ciudadano(telefono,
                                             registro_confirmado_en=datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 — la colaboración ya está guardada
            logger.exception("colaboración: no se pudo marcar el perfil como confirmado")

    if apps_script.esta_configurado():
        try:
            apps_script.enviar(registro["nombres"], registro["barrio"], borrador["ayuda"],
                               telefono)
        except Exception:  # noqa: BLE001
            logger.exception("colaboración: falló la copia al Apps Script")
    return {"ok": True, "error": None, "nombre": nombre}


def _log(nombre: str, resultado: dict) -> dict:
    logger.info("%s: ok=%s error=%s", nombre, resultado.get("ok"), resultado.get("error"))
    return resultado


@tool
def preparar_colaboracion(ayuda: str, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Guarda como borrador cómo quiere participar la persona o qué propone.

    Úsala cuando la persona contó su idea. Si la modifica, vuelve a llamarla con el
    texto nuevo.

    Args:
        ayuda: Cómo quiere participar o qué idea o propuesta comparte, con sus
            palabras.

    Returns:
        ok=true -> muestra el texto (`resumen.ayuda`) y pregunta si está correcto.
        Todavía NO está enviada. error IDEA_CORTA -> pide que cuente un poco más.
    """
    ctx = runtime.context or ConversacionContext()
    return _log("preparar_colaboracion",
                preparar(ayuda, ctx.telefono, nombre_perfil=ctx.nombre_perfil, canal=ctx.canal))


@tool
def confirmar_colaboracion(confirma: bool, runtime: ToolRuntime[ConversacionContext]) -> dict:
    """Envía al equipo (confirma=true) o descarta (confirma=false) la idea en borrador.

    confirma=true con "Está correcta, enviar al equipo", y también para "Reintentar"
    o "Comprobar estado": nunca duplica. confirma=false solo si la persona pide
    expresamente descartar la idea.

    Returns:
        ok=true -> quedó registrada (o ya lo estaba: `ya_registrada`). error FALLO ->
        no se completó. RESULTADO_INCIERTO -> no se sabe si quedó: no digas que sí.
    """
    ctx = runtime.context or ConversacionContext()
    return _log("confirmar_colaboracion", confirmar(confirma, ctx.telefono))
