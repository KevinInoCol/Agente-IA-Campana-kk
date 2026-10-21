"""
Envío del simpatizante a Google Apps Script (paso 4 del guion "Quiero sumarme").
================================================================================

El guion del cliente define el guardado como "Webhook POST a Google Apps Script con:
nombre, barrio, ayuda, telefono (waId)". Normalmente ese Apps Script escribe en una
Google Sheet que el equipo de campaña mira a diario.

Es OPCIONAL: solo se usa si `APPS_SCRIPT_WEBHOOK_URL` está definida. La copia que
manda es Postgres; esta es la que ve el equipo. Por eso un fallo aquí se registra
pero no hace fallar el guardado si Postgres lo guardó (ver la tool).

Formato del cuerpo: los cuatro campos del guion, con esos nombres exactos, más la
fecha. Si el Apps Script del cliente espera otros nombres, se cambian aquí.
"""

import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

APPS_SCRIPT_WEBHOOK_URL = os.getenv("APPS_SCRIPT_WEBHOOK_URL", "").strip()

# Apps Script suele tardar 1-3 s en arrancar en frío.
TIMEOUT_SEGUNDOS = 15


def esta_configurado() -> bool:
    return bool(APPS_SCRIPT_WEBHOOK_URL)


def enviar(nombre: str, barrio: str, ayuda: str, telefono: str | None) -> None:
    """POST del simpatizante al Apps Script. Lanza excepción si no responde 2xx.

    `follow_redirects=True` es obligatorio: un Web App de Apps Script responde 302
    hacia `script.googleusercontent.com` con el resultado.
    """
    cuerpo = {
        "nombre": nombre,
        "barrio": barrio,
        "ayuda": ayuda,
        "telefono": telefono or "",
        "fecha": datetime.now(timezone.utc).isoformat(),
    }
    respuesta = httpx.post(
        APPS_SCRIPT_WEBHOOK_URL,
        json=cuerpo,
        timeout=TIMEOUT_SEGUNDOS,
        follow_redirects=True,
    )
    respuesta.raise_for_status()
