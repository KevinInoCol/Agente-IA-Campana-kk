"""
Teléfono de contacto: validación, normalización y máscara (dominio PURO).
=========================================================================

El flujo v2 pide un "teléfono de contacto" APARTE del número de WhatsApp de la
conversación (F05: "mantenerlo distinguible"). Se normaliza a dígitos con código de
país, el mismo formato del número de WhatsApp, para poder compararlos:

    "099 123 4567"      -> "593991234567"   (celular, 10 dígitos con el 0)
    "+593 99 123 4567"  -> "593991234567"
    "04 2345678"        -> "59342345678"    (fijo, 9 dígitos con el 0)
    "+51 987 654 321"   -> "51987654321"    (extranjero: con "+" y 8 a 15 dígitos)

Validación laxa a propósito, como la del correo: se rechaza lo que claramente no es
un teléfono, no lo que no reconocemos.
"""

import re

_SOLO_DIGITOS = re.compile(r"\D")


def normalizar_telefono(texto: str | None) -> str | None:
    """Teléfono en dígitos con código de país, o None si no parece un teléfono."""
    crudo = (texto or "").strip()
    if not crudo:
        return None
    internacional = crudo.startswith("+") or crudo.startswith("00")
    digitos = _SOLO_DIGITOS.sub("", crudo)
    if crudo.startswith("00"):
        digitos = digitos[2:]

    if digitos.startswith("593"):
        nacional = digitos[3:]
        return digitos if len(nacional) in (8, 9) else None
    if not internacional:
        # Ecuador: celular 09XXXXXXXX (10) o fijo 0[2-7]XXXXXXX (9).
        if re.fullmatch(r"09\d{8}|0[2-7]\d{7}", digitos):
            return "593" + digitos[1:]
        return None
    return digitos if 8 <= len(digitos) <= 15 else None


def enmascarar_telefono(telefono: str | None) -> str | None:
    """'593991234567' -> '*********567' (solo los 3 últimos dígitos)."""
    if not telefono:
        return None
    return "*" * max(len(telefono) - 3, 0) + telefono[-3:]


def enmascarar_correo(correo: str | None) -> str | None:
    """'carlos.mora@gmail.com' -> 'c***@gmail.com'."""
    if not correo or "@" not in correo:
        return None
    local, dominio = correo.split("@", 1)
    return f"{local[:1]}***@{dominio}"
