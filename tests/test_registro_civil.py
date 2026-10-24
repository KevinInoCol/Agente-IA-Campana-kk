"""Adapter de la DINARDAP vía ventanilla (respuestas verificadas en MAD).

Nunca sale a la red: se simula `httpx.get`. El uso dentro del registro se prueba en
`test_registro.py`.
"""

import httpx
import pytest

from integraciones.persona import adaptador

EXISTE = "0915450001"        # está en fixtures_personas.json


# --- Adapter de la ventanilla (respuestas verificadas en MAD) -------------------

class _Resp:
    def __init__(self, status, cuerpo=None):
        self.status_code = status
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


@pytest.fixture
def ventanilla(fuente, monkeypatch):
    fuente("ventanilla")
    monkeypatch.setattr(adaptador, "VENTANILLA_USER", "u")
    monkeypatch.setattr(adaptador, "VENTANILLA_PASSWORD", "p")

    def responder(status, cuerpo=None):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(status, cuerpo))
    return responder


def test_ventanilla_encontrada(ventanilla):
    ventanilla(200, {"cedula": EXISTE, "nombre": "MORA PEREZ ANA LUCIA", "sexo": "MUJER"})
    r = adaptador.consultar_persona(EXISTE)
    assert r["ok"] and r["data"]["first_name"] == "ANA LUCIA"


@pytest.mark.parametrize("status,cuerpo,codigo", [
    (500, None, "PERSON_NOT_FOUND"),            # trampa: no existe = HTTP 500
    (200, {"nombre": "X Y Z", "fechaDefuncion": "01/01/2020"}, "PERSON_DECEASED"),
    (401, None, "SOURCE_REJECTED"),
    (200, {"numeroRuc": "0915450001001"}, "SOURCE_MALFORMED"),
])
def test_ventanilla_fallos(ventanilla, status, cuerpo, codigo):
    ventanilla(status, cuerpo)
    assert adaptador.consultar_persona(EXISTE)["error"]["code"] == codigo


def test_ventanilla_sin_credenciales(fuente, monkeypatch):
    fuente("ventanilla")
    monkeypatch.setattr(adaptador, "VENTANILLA_USER", "")
    assert adaptador.consultar_persona(EXISTE)["error"]["code"] == "SOURCE_NOT_CONFIGURED"


def test_cedula_invalida_no_sale_a_la_red(fuente, monkeypatch):
    fuente("ventanilla")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("no debía salir a la red"))
    assert adaptador.consultar_persona("0915450002")["error"]["code"] == "NATIONAL_ID_INVALID"
