"""
Cliente de Model Armor: revisa un mensaje y devuelve un veredicto.
==================================================================

API verificada contra la documentación oficial y el SDK instalado
(`google-cloud-modelarmor` 0.7.3, 2026-10-23):
  - `modelarmor_v1.ModelArmorClient` con `api_endpoint` REGIONAL
    (`modelarmor.<región>.rep.googleapis.com`), obligatorio para sanitizar.
  - `SanitizeUserPromptRequest(name=<plantilla>, user_prompt_data=DataItem(text=...))`.
  - Resultado: `sanitization_result.filter_match_state` (MATCH_FOUND / NO_MATCH_FOUND)
    y `filter_results`, un mapa cuyas entradas traen UNO de estos campos:
    `pi_and_jailbreak_filter_result`, `rai_filter_result`,
    `malicious_uri_filter_result`, `csam_filter_filter_result`, `sdp_filter_result`,
    `virus_scan_filter_result`; cada uno con su `match_state`.

Qué bloquea y con qué motivo (los textos están en input_guardrails/respuesta_bloqueada):
  - inyección / jailbreak            -> "jailbreak_ia"
  - IA responsable o CSAM            -> "contenido_ia_bloqueado"
  - URL maliciosa                    -> "dominio_bloqueado"
  - datos sensibles (SDP)            -> NO se bloquea: el bot PIDE cédula y correo en el
                                        registro. El filtro SDP no debe activarse en la
                                        plantilla; si lo estuviera, se ignora aquí.

Las respuestas triviales del menú ("1", "sí", "gracias") no se mandan: no pueden
esconder un ataque y son la mayoría de los mensajes (menos latencia y menos cuota).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass

from . import config

logger = logging.getLogger(__name__)

# Campo del FilterResult -> motivo de bloqueo. Lo que no está aquí (sdp, virus_scan)
# no bloquea.
_FILTROS_QUE_BLOQUEAN = {
    "pi_and_jailbreak_filter_result": "jailbreak_ia",
    "rai_filter_result": "contenido_ia_bloqueado",
    "csam_filter_filter_result": "contenido_ia_bloqueado",
    "malicious_uri_filter_result": "dominio_bloqueado",
}

_RESPUESTA_TRIVIAL = re.compile(
    r"^\s*(?:\d{1,2}[.)]?|s[ií]|no|ok|dale|gracias|hola|buenas|listo|perfecto)\s*[.!]*\s*$",
    re.IGNORECASE,
)

_cliente = None
_lock = threading.Lock()


@dataclass(frozen=True)
class Veredicto:
    bloquear: bool
    motivo: str = ""


def es_respuesta_trivial(texto: str) -> bool:
    """True si el mensaje es una opción del menú o una palabra de cortesía."""
    return bool(_RESPUESTA_TRIVIAL.match(texto or ""))


def _crear_cliente():
    """Cliente del SDK (singleton). Import perezoso: sin el SDK, el resto del agente
    sigue funcionando mientras Model Armor no esté activado."""
    global _cliente
    if _cliente is not None:
        return _cliente
    with _lock:
        if _cliente is not None:
            return _cliente
        from google.api_core.client_options import ClientOptions
        from google.cloud import modelarmor_v1

        kwargs = {
            "transport": "rest",
            "client_options": ClientOptions(
                api_endpoint=f"modelarmor.{config.MODEL_ARMOR_LOCATION}.rep.googleapis.com"
            ),
        }
        if config.MODEL_ARMOR_CREDENTIALS_JSON:
            from google.oauth2 import service_account

            kwargs["credentials"] = service_account.Credentials.from_service_account_info(
                json.loads(config.MODEL_ARMOR_CREDENTIALS_JSON)
            )
        _cliente = modelarmor_v1.ModelArmorClient(**kwargs)
        return _cliente


def interpretar(resultado) -> Veredicto:
    """De `sanitization_result` a un veredicto (función pura, testeable sin GCP)."""
    from google.cloud import modelarmor_v1

    if resultado.filter_match_state != modelarmor_v1.FilterMatchState.MATCH_FOUND:
        return Veredicto(False)
    for clave, filtro in resultado.filter_results.items():
        for campo, motivo in _FILTROS_QUE_BLOQUEAN.items():
            sub = getattr(filtro, campo, None)
            if sub is not None and sub.match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                logger.warning("[MODEL ARMOR] Bloqueado por el filtro '%s'", clave)
                return Veredicto(True, motivo)
    # Solo coincidió un filtro que no bloquea (p. ej. SDP por la cédula del registro).
    return Veredicto(False)


def revisar(texto: str) -> Veredicto:
    """Revisa un mensaje entrante con Model Armor. Nunca lanza excepción."""
    if not texto or not texto.strip() or es_respuesta_trivial(texto):
        return Veredicto(False)
    if not config.esta_configurado():
        logger.warning("[MODEL ARMOR] Sin configurar (MODEL_ARMOR_PROJECT_ID / "
                       "MODEL_ARMOR_TEMPLATE_ID): el mensaje pasa sin revisar.")
        return Veredicto(False)
    try:
        from google.cloud import modelarmor_v1

        respuesta = _crear_cliente().sanitize_user_prompt(
            request=modelarmor_v1.SanitizeUserPromptRequest(
                name=config.nombre_plantilla(),
                user_prompt_data=modelarmor_v1.DataItem(text=texto),
            ),
            timeout=config.MODEL_ARMOR_TIMEOUT_SECONDS,
        )
        return interpretar(respuesta.sanitization_result)
    except Exception as e:  # noqa: BLE001 — un filtro externo caído no corta la conversación
        if config.MODEL_ARMOR_FAIL_CLOSED:
            logger.error("[MODEL ARMOR] No disponible (%s); fail-close: se bloquea.",
                         type(e).__name__)
            return Veredicto(True, "servicio_no_disponible")
        logger.warning("[MODEL ARMOR] No disponible (%s); el mensaje sigue (fail-open).",
                       type(e).__name__)
        return Veredicto(False)
