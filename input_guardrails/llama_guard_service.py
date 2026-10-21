"""
HybridSafetyService — Capas 7 y 8 de Seguridad (IA via GROQ)

Combina dos modelos especializados de moderación:
  Capa 7 → Llama Prompt Guard 2 (86M params, Meta)
            Detecta Jailbreak / Prompt Injection
            Respuesta: probabilidad de ataque (0..1) como string; se bloquea si
            es >= PROMPT_GUARD_THRESHOLD (0.5)

  Capa 8 → GPT-OSS-Safeguard 20B (OpenAI, vía Groq)
            Detecta NSFW, Hate, Violence, Self-Harm
            Respuesta: "safe" o "unsafe\nS1, S2, ..."
            NOTA: reemplaza a meta-llama/llama-guard-4-12b, deprecado por Groq
            (apagado 2026-03-05). El id vigente es openai/gpt-oss-safeguard-20b.

Estrategia:
  - Ejecuta Prompt Guard primero (rápido, enfocado en ataques)
  - Si pasa, ejecuta GPT-OSS-Safeguard (profundo, contenido NSFW)

Adaptación de async → sync respecto al código original del amigo,
porque el agente es síncrono (se invoca en un threadpool desde el canal).

Requiere: pip install groq
          GROQ_API_KEY en el archivo .env

Autor: Ing. Kevin Inofuente Colque - DataPath
"""

import os
import re
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# Umbral de la Capa 7: Llama Prompt Guard 2 (vía Groq) devuelve la PROBABILIDAD
# (0..1) de que el texto sea un ataque (jailbreak/injection), como string —NO la
# palabra "MALICIOUS"—. Se bloquea si la probabilidad supera este umbral.
PROMPT_GUARD_THRESHOLD = 0.5

# Política de moderación para la Capa 8. gpt-oss-safeguard-20b es un modelo
# "trae-tu-política": sin una política en el system prompt responde como chat
# (no clasifica). Esta política fija la taxonomía de riesgos y el FORMATO de
# salida exacto que espera el parser de `validate_toxicity` (`safe` / `unsafe`).
MODERATION_POLICY = """Eres un clasificador de seguridad de contenido. Analiza \
ÚNICAMENTE el mensaje del usuario y decide si incumple alguna de estas categorías:
S1 Crímenes violentos
S2 Crímenes no violentos
S3 Crímenes sexuales
S4 Explotación sexual infantil
S5 Difamación
S6 Asesoramiento especializado peligroso
S7 Privacidad
S8 Propiedad intelectual
S9 Armas indiscriminadas
S10 Odio
S11 Suicidio y autolesión
S12 Contenido sexual
S13 Elecciones

Responde SOLO con el veredicto, sin explicaciones ni razonamiento:
- Si es seguro, responde exactamente: safe
- Si incumple una o más categorías, responde 'unsafe' y en la línea siguiente \
los códigos separados por coma. Ejemplo:
unsafe
S1,S10"""


class HybridSafetyService:
    """
    Singleton service para verificaciones de seguridad IA usando Groq.

    Combina dos modelos especializados:
      1. Llama Prompt Guard 2 (86M)  → Detecta Jailbreak/Prompt Injection
      2. GPT-OSS-Safeguard 20B       → Detecta NSFW, Hate, Violence, Self-Harm

    Estrategia:
      - Ejecuta Prompt Guard primero (rápido, enfocado en ataques)
      - Si pasa y check_nsfw está activo, ejecuta GPT-OSS-Safeguard
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Inicializa el cliente Groq (llamado una sola vez por el singleton)."""
        self.groq_client = None

        try:
            from groq import Groq
            self.groq_api_key = os.getenv("GROQ_API_KEY")

            if self.groq_api_key:
                self.groq_client = Groq(api_key=self.groq_api_key)
                logger.info("[SAFETY] ✅ Groq client inicializado")
            else:
                logger.warning("[SAFETY] ⚠️ GROQ_API_KEY no encontrada en el entorno")

        except ImportError:
            logger.error("[SAFETY] ❌ Groq SDK no instalado. Ejecuta: pip install groq")

        logger.info(f"[SAFETY] Servicio listo (Groq disponible: {bool(self.groq_client)})")

    def _call_model(self, model: str, message: str, system: Optional[str] = None) -> str:
        """
        Llama al modelo en Groq de forma síncrona.

        `system` (opcional): mensaje de sistema. Necesario para gpt-oss-safeguard,
        que requiere una política en el system prompt para clasificar.
        """
        mensajes = []
        if system:
            mensajes.append({"role": "system", "content": system})
        mensajes.append({"role": "user", "content": message})
        chat_completion = self.groq_client.chat.completions.create(
            messages=mensajes,
            model=model,
            temperature=0.0,
        )
        return (chat_completion.choices[0].message.content or "").strip()

    # ----------------------------------------------------------
    # Capa 7: Llama Prompt Guard 2 — Jailbreak / Injection
    # ----------------------------------------------------------
    def validate_jailbreak(
        self,
        message: str,
        fail_close: bool = True,
    ) -> Tuple[bool, str]:
        """
        Verifica Jailbreak/Injection usando Llama Prompt Guard 2.

        Args:
            message:    Texto del usuario a verificar.
            fail_close: Si True, bloquea el mensaje cuando el servicio falla.
                        Si False, deja pasar (fail-open).

        Returns:
            (is_malicious, reason)
        """
        if not self.groq_client:
            return False, ""

        try:
            result = self._call_model("meta-llama/llama-prompt-guard-2-86m", message)

            # Prompt Guard 2 devuelve la PROBABILIDAD de ataque (0..1) como string.
            # Se extrae el primer número (tolera formatos como "0.99 (attack)");
            # el regex de dígitos descarta de por sí NaN/inf. Fallback textual.
            m = re.search(r"\d*\.?\d+", result)
            es_ataque = (
                float(m.group()) >= PROMPT_GUARD_THRESHOLD
                if m else "MALICIOUS" in result.upper()
            )

            if es_ataque:
                logger.warning("[SAFETY] 🚨 Prompt Injection/Jailbreak detectado (Prompt Guard 2)")
                return True, "jailbreak_ia"

            return False, ""

        except Exception as e:
            if fail_close:
                logger.error(f"[SAFETY] ❌ Fail-Close activado en Jailbreak check: {e}")
                return True, "servicio_no_disponible"
            return False, ""

    # ----------------------------------------------------------
    # Capa 8: GPT-OSS-Safeguard 20B — NSFW / Hate / Violence / Self-Harm
    # (reemplazo del deprecado meta-llama/llama-guard-4-12b)
    # ----------------------------------------------------------
    def validate_toxicity(
        self,
        message: str,
        skip_categories: Optional[List[str]] = None,
        fail_close: bool = True,
    ) -> Tuple[bool, str]:
        """
        Verifica NSFW/Hate/Violence usando GPT-OSS-Safeguard 20B.

        Args:
            message:         Texto del usuario a verificar.
            skip_categories: Categorías de Llama Guard a ignorar.
                             Ejemplo: ['S7'] para ignorar privacidad.
            fail_close:      Si True, bloquea cuando el servicio falla.

        Returns:
            (is_unsafe, reason)

        Categorías de moderación (taxonomía de hazards S1–S13):
            S1  Violent Crimes
            S2  Non-Violent Crimes
            S3  Sex-Related Crimes
            S4  Child Sexual Exploitation
            S5  Defamation
            S6  Specialized Advice
            S7  Privacy
            S8  Intellectual Property
            S9  Indiscriminate Weapons
            S10 Hate
            S11 Suicide & Self-Harm
            S12 Sexual Content
            S13 Elections
        """
        if not self.groq_client:
            return False, ""

        skip_categories = [c.upper() for c in (skip_categories or [])]

        try:
            # gpt-oss-safeguard requiere la política en el system prompt.
            result = self._call_model(
                "openai/gpt-oss-safeguard-20b", message, system=MODERATION_POLICY
            )
            low = result.lower()

            # Parseo robusto (tolera razonamiento previo del modelo): se considera
            # inseguro si aparece el token 'unsafe'. Las categorías Sx se extraen
            # con regex del texto completo (S1..S13). 'safe' es subcadena de
            # 'unsafe', por eso se chequea 'unsafe' primero.
            if "unsafe" in low:  # subcadena: tolera 'unsafe' pegado a la categoría
                categories = [c.upper() for c in re.findall(r"\bs(?:1[0-3]|[1-9])\b", low)]
                if categories:
                    active_categories = [c for c in categories if c not in skip_categories]
                    if not active_categories:
                        logger.info(f"[SAFETY] ⚠️ Categorías ignoradas: {categories} (config usuario)")
                        return False, ""
                    logger.warning(
                        f"[SAFETY] 🚨 Contenido bloqueado: {active_categories} (GPT-OSS-Safeguard 20B)"
                    )
                else:
                    # 'unsafe' sin categorías parseables: bloquear igual (conservador).
                    logger.warning("[SAFETY] 🚨 Contenido bloqueado (GPT-OSS-Safeguard 20B, sin categoría)")
                return True, "contenido_ia_bloqueado"

            return False, ""

        except Exception as e:
            if fail_close:
                logger.error(f"[SAFETY] ❌ Fail-Close activado en Toxicity check: {e}")
                return True, "servicio_no_disponible"
            return False, ""

    # ----------------------------------------------------------
    # Facade principal — ejecuta ambas capas en secuencia
    # ----------------------------------------------------------
    def validate_all(
        self,
        message: str,
        check_jailbreak: bool = True,
        check_nsfw: bool = True,
        skip_categories: Optional[List[str]] = None,
        fail_close: bool = True,
    ) -> Tuple[bool, str]:
        """
        Facade que ejecuta las validaciones secuencialmente.

        Args:
            check_jailbreak:  Activar Llama Prompt Guard 2.
            check_nsfw:       Activar GPT-OSS-Safeguard 20B.
            skip_categories:  Categorías de moderación a ignorar.
            fail_close:       Comportamiento ante fallos del servicio.

        Returns:
            (is_blocked, reason)
        """
        # Capa 7: Jailbreak (prioridad)
        if check_jailbreak:
            is_jailbreak, reason = self.validate_jailbreak(message, fail_close=fail_close)
            if is_jailbreak:
                return True, reason

        # Capa 8: Toxicidad (si pasó el jailbreak)
        if check_nsfw:
            is_toxic, reason = self.validate_toxicity(
                message,
                skip_categories=skip_categories,
                fail_close=fail_close,
            )
            if is_toxic:
                return True, reason

        return False, ""


# Alias para compatibilidad
LlamaGuardService = HybridSafetyService


def get_llama_guard_service() -> HybridSafetyService:
    """Retorna la instancia singleton de HybridSafetyService."""
    return HybridSafetyService()
