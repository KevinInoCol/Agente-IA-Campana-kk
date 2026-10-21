"""
Transcripción de notas de voz con Whisper (API de OpenAI).
==========================================================

Preprocesamiento de CANAL: convierte un audio entrante de WhatsApp a texto ANTES de
invocar al agente. No es una tool: el modelo nunca decide "transcribir".

A diferencia de MAD (que bajaba el audio de una URL de GoHighLevel y por eso tenía
que blindarse contra SSRF), aquí el audio llega como bytes desde SmartGob Conecta
(`smartgob_conecta.descargar_media`), así que no se descarga nada de URLs externas.

Modelo: `whisper-1` (WHISPER_MODEL en .env), con la misma OPENAI_API_KEY del agente.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

load_dotenv(find_dotenv())

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# Límite de whisper-1: la API rechaza archivos más grandes.
MAX_BYTES_AUDIO = 25 * 1024 * 1024

# mimetype -> extensión que entiende whisper-1. Las notas de voz de WhatsApp son
# "audio/ogg; codecs=opus".
_EXTENSIONES = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
}


@lru_cache(maxsize=1)
def _cliente() -> OpenAI:
    # timeout/retries acotados: corre en el threadpool de FastAPI.
    return OpenAI(timeout=45.0, max_retries=1)


def _extension(mimetype: str) -> str:
    base = (mimetype or "").split(";", 1)[0].strip().lower()
    return _EXTENSIONES.get(base, "ogg")


def transcribir_audio(contenido: bytes, mimetype: str = "audio/ogg") -> str:
    """Transcribe los bytes de un audio. Llamada BLOQUEANTE; lanza excepción si falla."""
    if len(contenido) > MAX_BYTES_AUDIO:
        raise ValueError(f"Audio excede el tamaño máximo soportado ({MAX_BYTES_AUDIO} bytes)")
    transcripcion = _cliente().audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=(f"audio.{_extension(mimetype)}", contenido),
        language="es",
    )
    return transcripcion.text.strip()
