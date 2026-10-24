"""
Adapter de la fuente de identidad: ¿existe esta cédula en el registro civil?
============================================================================

Copiado de MAD (`Backend/integraciones/persona/adaptador.py`), donde consulta la
DINARDAP en producción. Aquí solo quedan los modos que sirven a Toto, elegidos por
`PERSONA_FUENTE`:

    ninguna  (por defecto)  no se consulta nada: la cédula solo se valida con el
                            módulo 10, como antes. Así el agente sigue funcionando
                            igual mientras no haya credenciales.
    stub                    fixtures locales con cédulas SINTÉTICAS. Sin red (tests).
    ventanilla              DINARDAP vía la ventanilla municipal (rmpplayas), Basic
                            Auth. La misma fuente que usa MAD.

REGLAS (heredadas de MAD)
- **Nunca se consulta con una cédula inválida**: se valida antes de salir a la red.
- **La llamada sale del BACKEND, jamás del LLM.** Por eso esto no es una `@tool`: la
  llama `registrar_ciudadano` por dentro.
- **Credenciales por variable de entorno**, nunca en código ni en el repositorio.
- **Timeout siempre.** Un 4xx/500 de negocio no se reintenta.
- **La cédula NUNCA se escribe en un log**: se registra el `correlation_id`.
- Devuelve el envelope `{ok, data, error, meta}`.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

from dominio.identidad import validar_cedula

from .mapper import (
    PersonaDTO,
    PersonaFallecida,
    PersonaNoEncontrada,
    RespuestaIlegible,
    mapear_ventanilla_dinardap,
)
from .puerto import FuentePersona

load_dotenv(find_dotenv())

logger = logging.getLogger("integraciones.persona")

PERSONA_FUENTE = os.getenv("PERSONA_FUENTE", "ninguna").strip().lower()
PERSONA_TIMEOUT_MS = int(os.getenv("PERSONA_TIMEOUT_MS", "8000"))

VENTANILLA_URL = os.getenv(
    "VENTANILLA_DINARDAP_URL",
    "https://ventanilla.rmpplayas.gob.ec/services/api/dinardap/consulta",
)
VENTANILLA_USER = os.getenv("VENTANILLA_DINARDAP_USER", "")
VENTANILLA_PASSWORD = os.getenv("VENTANILLA_DINARDAP_PASSWORD", "")

FIXTURES = Path(__file__).parent / "fixtures_personas.json"


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------
def _meta(correlation_id: str, fuente: str) -> dict:
    return {"correlation_id": correlation_id, "source": fuente, "schema_version": "v1"}


def _ok(dto: PersonaDTO, correlation_id: str, fuente: str) -> dict:
    return {
        "ok": True,
        "data": {
            "national_id": dto.national_id,
            "first_name": dto.first_name,
            "full_name": dto.full_name,
            "raw_source_ref": dto.raw_source_ref,
        },
        "error": None,
        "meta": _meta(correlation_id, fuente),
    }


def _fallo(code: str, mensaje: str, reintentable: bool, correlation_id: str, fuente: str) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message_safe": mensaje, "retryable": reintentable},
        "meta": _meta(correlation_id, fuente),
    }


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
class AdaptadorStub(FuentePersona):
    """Fixtures locales con cédulas SINTÉTICAS. Nunca sale a la red."""

    nombre = "stub"

    def __init__(self, ruta: Path | None = None) -> None:
        self._datos = json.loads((ruta or FIXTURES).read_text(encoding="utf-8"))

    def consultar(self, cedula: str, correlation_id: str) -> dict:
        registro = self._datos.get(cedula)
        if registro is None:
            return _fallo("PERSON_NOT_FOUND", "La cédula no existe en la fuente.",
                          False, correlation_id, self.nombre)
        dto = PersonaDTO(national_id=cedula, first_name=registro["first_name"],
                         full_name=registro["full_name"],
                         raw_source_ref=f"stub:{correlation_id}")
        return _ok(dto, correlation_id, self.nombre)


class AdaptadorVentanilla(FuentePersona):
    """DINARDAP vía ventanilla municipal, Basic Auth desde el backend.

    Trampa verificada en MAD (2026-08-26): una cédula que NO existe responde
    **HTTP 500**, no 404. Por eso el 500 se traduce a `PERSON_NOT_FOUND` y no se
    reintenta: sería gastar llamadas contra el Registro Civil por cada persona que
    teclee mal su cédula.
    """

    nombre = "ventanilla"

    def consultar(self, cedula: str, correlation_id: str) -> dict:
        if not (VENTANILLA_USER and VENTANILLA_PASSWORD):
            return _fallo("SOURCE_NOT_CONFIGURED",
                          "Faltan VENTANILLA_DINARDAP_USER / _PASSWORD.",
                          False, correlation_id, self.nombre)
        import httpx

        url = f"{VENTANILLA_URL.rstrip('/')}/{cedula}"
        try:
            r = httpx.get(url, auth=(VENTANILLA_USER, VENTANILLA_PASSWORD),
                          timeout=PERSONA_TIMEOUT_MS / 1000)
        except Exception as e:  # noqa: BLE001 — fallo de red
            logger.warning("persona: fallo de red (corr=%s): %s", correlation_id,
                           type(e).__name__)
            return _fallo("SOURCE_UNAVAILABLE", f"Fuente inalcanzable ({type(e).__name__}).",
                          True, correlation_id, self.nombre)

        if r.status_code == 500:
            # Comportamiento verificado: 500 = la cédula no existe.
            logger.info("persona: no encontrada (corr=%s)", correlation_id)
            return _fallo("PERSON_NOT_FOUND", "La cédula no existe en la fuente.",
                          False, correlation_id, self.nombre)
        if r.status_code in (401, 403):
            logger.error("persona: credenciales rechazadas por la ventanilla (corr=%s)",
                         correlation_id)
            return _fallo("SOURCE_REJECTED", "Credenciales rechazadas por la fuente.",
                          False, correlation_id, self.nombre)
        if r.status_code >= 400:
            return _fallo("SOURCE_REJECTED", f"La fuente respondió {r.status_code}.",
                          False, correlation_id, self.nombre)

        try:
            dto = mapear_ventanilla_dinardap(r.json(), cedula, f"ventanilla:{correlation_id}")
        except PersonaFallecida:
            logger.info("persona: registro con defunción (corr=%s)", correlation_id)
            return _fallo("PERSON_DECEASED",
                          "El registro civil marca a la persona como fallecida.",
                          False, correlation_id, self.nombre)
        except PersonaNoEncontrada:
            return _fallo("PERSON_NOT_FOUND", "La cédula no existe en la fuente.",
                          False, correlation_id, self.nombre)
        except (RespuestaIlegible, ValueError) as e:
            logger.warning("persona: respuesta ilegible (corr=%s): %s", correlation_id, e)
            return _fallo("SOURCE_MALFORMED", "La respuesta de la fuente no es interpretable.",
                          False, correlation_id, self.nombre)

        return _ok(dto, correlation_id, self.nombre)


_ADAPTADORES = {"stub": AdaptadorStub, "ventanilla": AdaptadorVentanilla}
_instancia: FuentePersona | None = None


def esta_activa() -> bool:
    """True si hay que consultar la fuente (PERSONA_FUENTE distinta de `ninguna`)."""
    return PERSONA_FUENTE != "ninguna"


def obtener_fuente() -> FuentePersona:
    """Devuelve el adapter configurado en `PERSONA_FUENTE` (singleton perezoso)."""
    global _instancia
    if _instancia is None:
        clase = _ADAPTADORES.get(PERSONA_FUENTE)
        if clase is None:
            raise ValueError(
                f"PERSONA_FUENTE inválida: {PERSONA_FUENTE!r}. "
                f"Opciones: ninguna, {', '.join(_ADAPTADORES)}."
            )
        _instancia = clase()
    return _instancia


def consultar_persona(cedula_cruda: str, correlation_id: str | None = None) -> dict:
    """Cédula -> PersonaDTO, en envelope. Valida ANTES de salir a la red."""
    correlation_id = correlation_id or str(uuid.uuid4())

    v = validar_cedula(cedula_cruda)
    if not v.valida:
        return _fallo("NATIONAL_ID_INVALID", f"Cédula inválida: {v.motivo}.",
                      False, correlation_id, PERSONA_FUENTE)

    return obtener_fuente().consultar(v.normalizada, correlation_id)
