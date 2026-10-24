"""Consulta al registro civil (DINARDAP) dentro de `registrar_ciudadano`.

Nunca sale a la red: se usa el adapter `stub` (cédulas sintéticas) o se simula
`httpx.get` para el de la ventanilla.
"""

import httpx
import pytest

from integraciones.persona import adaptador
from tests.test_registro import base_simulada  # noqa: F401 — fixture compartida
from tools.registro_tool import registrar_datos

EXISTE = "0915450001"        # está en fixtures_personas.json
NO_EXISTE = "0915450019"     # válida (módulo 10) pero no está en los fixtures
TEL = "593900000010"


@pytest.fixture
def fuente(monkeypatch):
    """Cambia PERSONA_FUENTE y reinicia el singleton del adapter."""
    def usar(nombre):
        monkeypatch.setattr(adaptador, "PERSONA_FUENTE", nombre)
        monkeypatch.setattr(adaptador, "_instancia", None)
    return usar


# --- Registro ------------------------------------------------------------------

def test_cedula_que_existe_se_guarda_verificada(base_simulada, fuente):
    fuente("stub")
    r = registrar_datos(cedula=EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
    assert r["ok"] and r["registro_completo"] and r["error"] is None
    assert base_simulada[TEL]["cedula_verificada"] is True


def test_cedula_que_no_existe_no_se_guarda_y_el_resto_si(base_simulada, fuente):
    fuente("stub")
    r = registrar_datos(cedula=NO_EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
    assert r["ok"] and not r["registro_completo"]
    assert r["error"] == "CEDULA_NO_EXISTE" and "cedula" in r["faltan"]
    assert base_simulada[TEL]["cedula"] is None
    assert base_simulada[TEL]["barrio"] == "Urdesa"


def test_solo_una_cedula_que_no_existe_no_guarda_nada(base_simulada, fuente):
    fuente("stub")
    r = registrar_datos(cedula=NO_EXISTE, telefono=TEL)
    assert not r["ok"] and r["error"] == "CEDULA_NO_EXISTE"
    assert TEL not in base_simulada


def test_fuente_caida_guarda_la_cedula_sin_verificar(base_simulada, fuente, monkeypatch):
    fuente("ventanilla")
    monkeypatch.setattr(adaptador, "VENTANILLA_USER", "u")
    monkeypatch.setattr(adaptador, "VENTANILLA_PASSWORD", "p")

    def caida(*a, **k):
        raise httpx.ConnectTimeout("timeout")
    monkeypatch.setattr(httpx, "get", caida)

    r = registrar_datos(cedula=EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
    assert r["ok"] and r["registro_completo"] and r["error"] is None
    assert base_simulada[TEL]["cedula"] == EXISTE
    assert base_simulada[TEL]["cedula_verificada"] is False


def test_apagada_no_consulta(base_simulada, fuente, monkeypatch):
    fuente("ninguna")
    monkeypatch.setattr(adaptador, "consultar_persona",
                        lambda *a, **k: pytest.fail("no debía consultar"))
    r = registrar_datos(cedula=NO_EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
    assert r["ok"] and r["registro_completo"]
    assert base_simulada[TEL]["cedula_verificada"] is None


def test_cedula_ya_verificada_no_se_vuelve_a_consultar(base_simulada, fuente, monkeypatch):
    fuente("stub")
    registrar_datos(cedula=EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
    monkeypatch.setattr(adaptador, "consultar_persona",
                        lambda *a, **k: pytest.fail("no debía consultar otra vez"))
    r = registrar_datos(cedula=EXISTE, correo="ana@gmail.com", telefono=TEL)
    assert r["ok"] and base_simulada[TEL]["cedula_verificada"] is True


def test_correo_despues_conserva_la_verificacion(base_simulada, fuente):
    fuente("stub")
    registrar_datos(cedula=EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
    registrar_datos(correo="ana@gmail.com", telefono=TEL)
    assert base_simulada[TEL]["cedula_verificada"] is True


def test_la_cedula_no_aparece_en_los_logs(base_simulada, fuente, caplog):
    fuente("stub")
    with caplog.at_level("DEBUG"):
        registrar_datos(cedula=NO_EXISTE, nombres="Ana Mora", barrio="Urdesa", telefono=TEL)
        registrar_datos(cedula=EXISTE, telefono=TEL)
    assert NO_EXISTE not in caplog.text and EXISTE not in caplog.text


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
