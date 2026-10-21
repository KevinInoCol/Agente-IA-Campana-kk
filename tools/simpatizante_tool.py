"""
Tool: guardar un simpatizante (cierre del flujo "Quiero sumarme").
==================================================================

Es el paso 4 del guion del cliente: con nombre, barrio y cómo quiere ayudar, guarda
a la persona junto a su número de WhatsApp.

POR QUÉ EL TELÉFONO NO ES UN ARGUMENTO
--------------------------------------
Todo argumento de una tool lo escribe el modelo. Si el teléfono fuera uno, el modelo
podría inventarlo o guardar un número que la persona mencionó en el chat (el de un
vecino, por ejemplo). Lo pone el canal en el `ConversacionContext` desde el
número del webhook de SmartGob Conecta, y la tool lo lee de ahí.

DÓNDE SE GUARDA
---------------
1. Postgres (`<DB_SCHEMA>.simpatizantes`): la copia que manda.
2. Google Apps Script, si `APPS_SCRIPT_WEBHOOK_URL` está definida: la planilla que
   mira el equipo (es lo que pide el guion).

`ok=true` si quedó guardado en al menos un destino. Un fallo del Apps Script con
Postgres OK no se le reporta a la persona como error: el dato está a salvo y se
puede reenviar después. Sí queda en el log y en `destinos` del resultado.

El prompt solo envía SUMARME_CIERRE ("Recibí tu información...") si `ok` es true.
"""

import logging

from langchain.tools import ToolRuntime, tool

from agent_context import ConversacionContext
from simpatizantes import Simpatizante, apps_script, repositorio

logger = logging.getLogger(__name__)

# Topes de largo: lo que la persona escribe va directo a la base y a la planilla.
MAX_NOMBRE = 120
MAX_BARRIO = 120
MAX_AYUDA = 2000


def registrar_simpatizante(
    nombre: str,
    barrio: str,
    ayuda: str,
    telefono: str | None = None,
    nombre_perfil: str | None = None,
    canal: str = "whatsapp",
) -> dict:
    """Guarda el simpatizante en los destinos configurados. Testeable sin el LLM.

    Devuelve {"ok": bool, "destinos": {...}, "error": str | None}.
    """
    nombre = (nombre or "").strip()[:MAX_NOMBRE]
    barrio = (barrio or "").strip()[:MAX_BARRIO]
    ayuda = (ayuda or "").strip()[:MAX_AYUDA]

    faltan = [campo for campo, valor in
              (("nombre", nombre), ("barrio", barrio), ("ayuda", ayuda)) if not valor]
    if faltan:
        return {"ok": False, "destinos": {},
                "error": f"Faltan datos: {', '.join(faltan)}. Pídeselos a la persona."}

    if not repositorio.esta_configurado() and not apps_script.esta_configurado():
        logger.error("guardar_simpatizante: no hay ningún destino configurado "
                     "(ni base de datos ni APPS_SCRIPT_WEBHOOK_URL).")
        return {"ok": False, "destinos": {}, "error": "No hay dónde guardar los datos."}

    destinos: dict[str, str] = {}

    if repositorio.esta_configurado():
        try:
            id_ = repositorio.guardar(Simpatizante(
                nombre=nombre, barrio=barrio, ayuda=ayuda, telefono=telefono,
                nombre_perfil=nombre_perfil, canal=canal,
            ))
            destinos["postgres"] = f"ok (id {id_})"
        except Exception as e:  # noqa: BLE001 — se reporta como ok=false si no hay otro destino
            logger.exception("guardar_simpatizante: falló Postgres")
            destinos["postgres"] = f"error: {type(e).__name__}"

    if apps_script.esta_configurado():
        try:
            apps_script.enviar(nombre, barrio, ayuda, telefono)
            destinos["apps_script"] = "ok"
        except Exception as e:  # noqa: BLE001
            logger.exception("guardar_simpatizante: falló Apps Script")
            destinos["apps_script"] = f"error: {type(e).__name__}"

    ok = any(estado.startswith("ok") for estado in destinos.values())
    return {"ok": ok, "destinos": destinos,
            "error": None if ok else "No se pudo guardar en este momento."}


@tool
def guardar_simpatizante(
    nombre: str,
    barrio: str,
    ayuda: str,
    runtime: ToolRuntime[ConversacionContext],
) -> dict:
    """Guarda a la persona como simpatizante de Juan José Reyes Baquerizo.

    Úsala SOLO al final del flujo "Quiero sumarme", cuando ya tengas los tres datos.
    El número de WhatsApp lo agrega el sistema: no lo pidas ni lo pases.

    Args:
        nombre: Nombre de la persona, tal como lo escribió.
        barrio: Sector o barrio de Guayaquil desde donde escribe, tal como lo escribió.
        ayuda: Cómo quiere sumarse o qué idea quiere compartir, con sus palabras.

    Returns:
        {"ok": true, ...} si quedó guardado. Con "ok": false NO confirmes el registro.
    """
    ctx = runtime.context or ConversacionContext()
    resultado = registrar_simpatizante(
        nombre=nombre,
        barrio=barrio,
        ayuda=ayuda,
        telefono=ctx.telefono,
        nombre_perfil=ctx.nombre_perfil,
        canal=ctx.canal,
    )
    # Log sin PII: solo el resultado por destino.
    logger.info("guardar_simpatizante: ok=%s destinos=%s", resultado["ok"], resultado["destinos"])
    return resultado
