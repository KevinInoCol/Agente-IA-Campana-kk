"""
PiiDetector — Capa 5 de Seguridad: Detección de Datos Personales Sensibles (PII)

Adaptado al caso de uso de ECUADOR (heredado del bot MAD).

Entidades detectadas:
  - CEDULA_EC   → Cédula de identidad ecuatoriana (10 dígitos, validación módulo 10)
  - RUC_EC      → Registro Único de Contribuyentes (13 dígitos, estructura EC)
  - EMAIL       → Correo electrónico
  - PHONE_EC    → Teléfono ecuatoriano (+593 / 0, celular 09… o fijo 0[2-7]…)
  - CREDIT_CARD → Números de tarjeta de crédito/débito (Visa/MC/Amex/Discover)

Acción configurable por entidad (`PII_CONFIG`):
  - "block"  → Bloquea el mensaje completo
  - "off"    → Desactiva la detección de esa entidad

Cédula y RUC se VALIDAN (no solo regex de N dígitos): así un número de trámite
u otra ristra de dígitos no dispara un falso positivo. Complementa al
`middleware/pii_middleware.py` (que REDACTA cédula/teléfono EC y deja pasar);
aquí la acción es BLOQUEAR.

Requiere: presidio-analyzer, presidio-anonymizer (opcional).
Si Presidio no está instalado, funciona con detección pura por regex + validación.

Autor: Ing. Kevin Inofuente Colque - DataPath
Adaptación a Ecuador: acople al agente.
"""

import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN DE ENTIDADES PII (Ecuador)
# ============================================================
PII_CONFIG: Dict[str, str] = {
    # El flujo de MAD PIDE la cédula (§5, paso IDENTIFY): bloquearla cortaría el
    # directamente en el chat, así que aquí NO se bloquea (antes: "block"). La cédula
    # sigue protegida por otra vía: `middleware/pii_middleware.py` la REDACTA (validada
    # módulo-10) antes del LLM, del checkpointer de Postgres y de las trazas de Langfuse
    # -> el número real nunca se persiste ni sale a observabilidad (postura LOPDP intacta).
    "CEDULA_EC":    "off",     # Cédula ecuatoriana — se recoge en el chat (registro)
    "RUC_EC":       "block",   # RUC — 13 dígitos (estructura EC)
    "EMAIL":        "block",   # Correo electrónico
    "PHONE_EC":     "block",   # Teléfono ecuatoriano
    "CREDIT_CARD":  "block",   # Tarjeta de crédito/débito
}


# ============================================================
# PATRONES REGEX POR ENTIDAD (candidatos; cédula/RUC se validan aparte)
# ============================================================
_PII_PATTERNS = {
    # Candidatos de 10 dígitos; la validación módulo-10 decide.
    "CEDULA_EC": re.compile(r"\b\d{10}\b"),
    # Candidatos de 13 dígitos; la validación estructural EC decide.
    "RUC_EC": re.compile(r"\b\d{13}\b"),
    "EMAIL": re.compile(
        r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
    ),
    # +593 / 593 / 0, celular (9 + 8 díg.) o fijo ([2-7] + 7 díg.), con separadores.
    "PHONE_EC": re.compile(
        r"(?:\+?593[\s.\-]?|\b0)(?:9(?:[\s.\-]?\d){8}|[2-7](?:[\s.\-]?\d){7})(?!\d)"
    ),
    "CREDIT_CARD": re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|"       # Visa
        r"5[1-5][0-9]{14}|"                     # MasterCard
        r"3[47][0-9]{13}|"                      # Amex
        r"6(?:011|5[0-9]{2})[0-9]{12})\b"       # Discover
    ),
}


# ============================================================
# VALIDADORES ECUADOR (reducen falsos positivos)
# ============================================================
_COEF_CEDULA = (2, 1, 2, 1, 2, 1, 2, 1, 2)


def _cedula_ec_valida(num: str) -> bool:
    """True si `num` (10 dígitos) es una cédula ecuatoriana válida (módulo 10).

    Provincia aceptada: 01-24 (provincias) y 30 (emitidas en el exterior). Se
    EXCLUYEN a propósito códigos especiales poco frecuentes (p. ej. 88/90);
    ampliarlos aquí requeriría alinear también `middleware/pii_middleware.py`
    para no divergir. Consecuencia: una cédula con esos códigos no se bloquearía
    (falso negativo acotado); el `PIIMiddleware` tampoco los cubre hoy.
    """
    if len(num) != 10 or not num.isdigit():
        return False
    provincia = int(num[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False
    if int(num[2]) >= 6:  # tercer dígito: persona natural
        return False
    total = 0
    for d, c in zip(num[:9], _COEF_CEDULA):
        p = int(d) * c
        if p >= 10:
            p -= 9
        total += p
    verificador = (10 - (total % 10)) % 10
    return verificador == int(num[9])


def _ruc_ec_valido(num: str) -> bool:
    """True si `num` (13 dígitos) tiene estructura de RUC ecuatoriano.

    Cubre los tres tipos por estructura (provincia válida + tipo por tercer
    dígito + establecimiento != 000):
      - Persona natural  (3er dígito 0-5): los primeros 10 son cédula válida.
      - Sector público    (3er dígito 6).
      - Persona jurídica  (3er dígito 9).
    Para persona natural se exige cédula válida (módulo 10); para 6/9 se valida
    estructura (checksum específico por tipo no implementado — suficiente para
    detección/bloqueo sin sobre-bloquear ristras arbitrarias de 13 dígitos).
    """
    if len(num) != 13 or not num.isdigit():
        return False
    provincia = int(num[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False
    tercer = int(num[2])
    if num[10:13] == "000":  # código de establecimiento inválido
        return False
    if tercer <= 5:
        return _cedula_ec_valida(num[:10])
    return tercer in (6, 9)


_VALIDADORES = {
    "CEDULA_EC": _cedula_ec_valida,
    "RUC_EC": _ruc_ec_valido,
}


class PiiDetector:
    """
    Detecta datos personales sensibles (PII) ecuatorianos en mensajes.

    Intenta usar Presidio (Microsoft) si está instalado.
    Si no está instalado, usa detección por regex + validación EC.
    """

    def __init__(self, config: Optional[Dict[str, str]] = None):
        self.config = config or PII_CONFIG
        self._presidio_analyzer = None
        self._init_presidio()

    def _init_presidio(self) -> None:
        """Intenta inicializar Presidio con reconocedores personalizados para Ecuador."""
        try:
            from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            provider = NlpEngineProvider(nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "es", "model_name": "es_core_news_sm"}],
            })
            nlp_engine = provider.create_engine()

            analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["es", "en"])

            # Reconocedor: Cédula Ecuador (10 dígitos)
            analyzer.registry.add_recognizer(PatternRecognizer(
                supported_entity="CEDULA_EC",
                patterns=[Pattern("CEDULA_EC", r"\b\d{10}\b", 0.6)],
                supported_language="es",
            ))

            # Reconocedor: RUC Ecuador (13 dígitos)
            analyzer.registry.add_recognizer(PatternRecognizer(
                supported_entity="RUC_EC",
                patterns=[Pattern("RUC_EC", r"\b\d{13}\b", 0.7)],
                supported_language="es",
            ))

            # Reconocedor: Teléfono Ecuador
            analyzer.registry.add_recognizer(PatternRecognizer(
                supported_entity="PHONE_EC",
                patterns=[Pattern(
                    "PHONE_EC",
                    r"(?:\+?593[\s.\-]?|\b0)(?:9(?:[\s.\-]?\d){8}|[2-7](?:[\s.\-]?\d){7})",
                    0.75,
                )],
                supported_language="es",
            ))

            self._presidio_analyzer = analyzer
            logger.info("[PII] Presidio inicializado con reconocedores para Ecuador (ES)")

        except ImportError:
            logger.info("[PII] Presidio no instalado — usando detección por regex + validación EC")
        except Exception as e:
            logger.warning(f"[PII] Presidio no disponible ({e}) — usando regex + validación EC")

    def detectar(self, texto: str) -> List[str]:
        """
        Analiza el texto y retorna lista de entidades PII detectadas
        cuya acción es 'block'.

        Returns:
            Lista de tipos detectados, ej: ["CEDULA_EC", "EMAIL"]. Vacía si es seguro.
        """
        if not texto or not texto.strip():
            return []

        activas = [k for k, v in self.config.items() if v == "block"]

        if self._presidio_analyzer:
            return self._detectar_con_presidio(texto, activas)
        return self._detectar_con_regex(texto, activas)

    def _detectar_con_presidio(self, texto: str, entidades: List[str]) -> List[str]:
        """Usa Presidio para detectar PII con NLP + regex; valida cédula/RUC."""
        try:
            resultados = self._presidio_analyzer.analyze(
                text=texto,
                language="es",
                entities=entidades,
            )
            detectadas = set()
            for r in resultados:
                if r.score < 0.6:
                    continue
                entidad = r.entity_type
                validador = _VALIDADORES.get(entidad)
                # Si la entidad tiene validador (cédula/RUC), exigir que el
                # fragmento realmente valide; evita bloquear cualquier ristra.
                if validador is not None:
                    fragmento = texto[r.start:r.end]
                    if not validador(re.sub(r"\D", "", fragmento)):
                        continue
                detectadas.add(entidad)
            if detectadas:
                logger.warning(f"[PII Presidio] Detectado: {sorted(detectadas)}")
            return sorted(detectadas)
        except Exception as e:
            logger.error(f"[PII Presidio] Error en análisis: {e}")
            return self._detectar_con_regex(texto, entidades)

    def _detectar_con_regex(self, texto: str, entidades: List[str]) -> List[str]:
        """Fallback: detección por regex + validación EC para cédula/RUC."""
        detectadas = []
        for entidad in entidades:
            pattern = _PII_PATTERNS.get(entidad)
            if not pattern:
                continue
            validador = _VALIDADORES.get(entidad)
            if validador is not None:
                # Entidad con validación (cédula/RUC): exigir un candidato válido.
                encontrada = any(validador(m.group()) for m in pattern.finditer(texto))
            else:
                encontrada = bool(pattern.search(texto))
            if encontrada:
                logger.warning(f"[PII Regex] {entidad} detectado en: {texto[:60]!r}")
                detectadas.append(entidad)
        return detectadas
