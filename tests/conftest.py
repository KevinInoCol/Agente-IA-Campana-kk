"""Fixtures compartidas: un repositorio falso en memoria y el modo de la DINARDAP."""

from datetime import datetime, timezone

import pytest

from integraciones.persona import adaptador
from simpatizantes import repositorio


class RepoFalso:
    """Imita las funciones de `simpatizantes.repositorio` que usan tools y middleware."""

    def __init__(self):
        self.ciudadanos: dict[str, dict] = {}
        self.colaboraciones: list[dict] = []
        self.fallar = None          # excepción a lanzar en confirmar_borrador

    def buscar_ciudadano(self, tel):
        if tel not in self.ciudadanos:
            return None
        return {k: self.ciudadanos[tel].get(k) for k in repositorio.COLUMNAS_LECTURA}

    def actualizar_ciudadano(self, tel, **campos):
        assert not set(campos) - set(repositorio.COLUMNAS_CIUDADANO), campos
        self.ciudadanos.setdefault(tel, {"nombres_confirmados": False}).update(campos)
        return self.buscar_ciudadano(tel)

    def borrar_ciudadano(self, tel):
        self.ciudadanos.pop(tel, None)

    def _borrador(self, tel):
        return next((c for c in self.colaboraciones
                     if c["telefono"] == tel and c["estado"] == "borrador"), None)

    def obtener_borrador(self, tel):
        b = self._borrador(tel)
        return {"id": b["id"], "ayuda": b["ayuda"]} if b else None

    def guardar_borrador(self, tel, ayuda, nombre, barrio, nombre_perfil, canal):
        b = self._borrador(tel)
        if b is None:
            b = {"id": len(self.colaboraciones) + 1, "telefono": tel, "estado": "borrador"}
            self.colaboraciones.append(b)
        b.update(ayuda=ayuda, nombre=nombre, barrio=barrio)
        return b["id"]

    def confirmar_borrador(self, tel, nombre, barrio, correo, telefono_contacto):
        if self.fallar:
            raise self.fallar
        b = self._borrador(tel)
        if b is None:
            return None
        b.update(estado="confirmada", confirmada_en=datetime.now(timezone.utc), nombre=nombre,
                 barrio=barrio, correo=correo, telefono_contacto=telefono_contacto)
        return b["id"]

    def cancelar_borrador(self, tel):
        b = self._borrador(tel)
        if b:
            b["estado"] = "cancelada"
        return b is not None

    def confirmada_reciente(self, tel, minutos):
        return next((c["id"] for c in reversed(self.colaboraciones)
                     if c["telefono"] == tel and c["estado"] == "confirmada"), None)


@pytest.fixture
def repo(monkeypatch):
    falso = RepoFalso()
    monkeypatch.setattr(repositorio, "esta_configurado", lambda: True)
    for nombre in ("buscar_ciudadano", "actualizar_ciudadano", "borrar_ciudadano",
                   "obtener_borrador", "guardar_borrador", "confirmar_borrador",
                   "cancelar_borrador", "confirmada_reciente"):
        monkeypatch.setattr(repositorio, nombre, getattr(falso, nombre))
    return falso


@pytest.fixture
def fuente(monkeypatch):
    """Cambia PERSONA_FUENTE y reinicia el singleton del adapter."""
    def usar(nombre):
        monkeypatch.setattr(adaptador, "PERSONA_FUENTE", nombre)
        monkeypatch.setattr(adaptador, "_instancia", None)
    usar("stub")
    return usar
