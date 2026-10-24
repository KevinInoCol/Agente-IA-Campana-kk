"""Registro de la bienvenida (guion v1): validación, combinación con lo guardado y estado.

Los tests de `integracion` escriben en la base de `DATABASE_URL` (en local, el Postgres
de docker-compose.dev.yml) y se saltan si no responde.
"""

import pytest

from dominio.correo import normalizar_email
from dominio.identidad import validar_cedula
from input_guardrails.input_guardrail import _guardrail
from middleware import describir_estado
from simpatizantes import repositorio
from tools import registrar_ciudadano
from tools.registro_tool import registrar_datos

# Cédula sintética válida (provincia 09, módulo 10); no es de nadie.
CEDULA = "0915450001"


def _base_disponible() -> bool:
    if not repositorio.esta_configurado():
        return False
    try:
        repositorio._get_pool()
        return True
    except Exception:  # noqa: BLE001
        return False


# --- Dominio ------------------------------------------------------------------

def test_cedula_valida_con_separadores():
    r = validar_cedula("091-545-0001")
    assert r.valida and r.normalizada == CEDULA


@pytest.mark.parametrize("cedula", ["0915450002", "1234", "9915450001", "0975450001"])
def test_cedula_invalida(cedula):
    assert not validar_cedula(cedula).valida


def test_correo():
    assert normalizar_email(" Ana.Lopez@Gmail.COM ") == "ana.lopez@gmail.com"
    assert normalizar_email("sin arroba") is None


# --- Tool (sin base: repositorio simulado) -------------------------------------

@pytest.fixture
def base_simulada(monkeypatch):
    filas = {}

    def buscar(tel):
        return dict(filas[tel]) if tel in filas else None

    def guardar(c):
        nuevo = c.telefono not in filas
        filas[c.telefono] = {"cedula": c.cedula, "nombres": c.nombres,
                             "correo": c.correo, "barrio": c.barrio,
                             "cedula_verificada": c.cedula_verificada}
        return 1, nuevo

    monkeypatch.setattr(repositorio, "esta_configurado", lambda: True)
    monkeypatch.setattr(repositorio, "buscar_ciudadano", buscar)
    monkeypatch.setattr(repositorio, "guardar_ciudadano", guardar)
    return filas


def test_registro_completo(base_simulada):
    r = registrar_datos(cedula=CEDULA, nombres="maría fernanda lópez", correo="Ana@Gmail.com",
                        barrio="Urdesa", telefono="593900000001")
    assert r["ok"] and r["registro_completo"] and r["nuevo"] and r["nombre"] == "María"
    assert base_simulada["593900000001"]["correo"] == "ana@gmail.com"


def test_por_partes_guarda_cada_dato_y_acumula(base_simulada):
    r = registrar_datos(nombres="Carlos Mora", barrio="Guasmo", telefono="593900000002")
    assert r["ok"] and not r["registro_completo"] and "cedula" in r["faltan"]
    assert base_simulada["593900000002"]["barrio"] == "Guasmo"   # guardado a medias
    r = registrar_datos(cedula=CEDULA, telefono="593900000002")  # solo el dato nuevo
    assert r["ok"] and r["registro_completo"] and r["faltan"] == ["correo (opcional)"]
    assert base_simulada["593900000002"]["nombres"] == "Carlos Mora"


def test_sin_telefono_exige_el_registro_completo(base_simulada):
    r = registrar_datos(nombres="Carlos Mora")
    assert not r["ok"] and not base_simulada


def test_completar_un_registro_existente_conserva_lo_guardado(base_simulada):
    registrar_datos(cedula=CEDULA, nombres="Carlos Mora", barrio="Guasmo", telefono="t1")
    r = registrar_datos(correo="carlos@outlook.es", telefono="t1")
    assert r["ok"] and not r["nuevo"]
    assert base_simulada["t1"] == {"cedula": CEDULA, "nombres": "Carlos Mora",
                                   "correo": "carlos@outlook.es", "barrio": "Guasmo",
                                   "cedula_verificada": None}


def test_cedula_invalida_no_se_guarda_pero_el_resto_si(base_simulada):
    r = registrar_datos(cedula="0915450002", nombres="Ana", barrio="Centro", telefono="t2")
    assert r["ok"] and not r["registro_completo"] and r["error"] == "CEDULA_INVALIDA"
    assert base_simulada["t2"]["cedula"] is None and base_simulada["t2"]["nombres"] == "Ana"


def test_solo_una_cedula_invalida_no_guarda_nada(base_simulada):
    r = registrar_datos(cedula="0915450002", telefono="t4")
    assert not r["ok"] and r["error"] == "CEDULA_INVALIDA" and "t4" not in base_simulada


def test_correo_invalido_no_impide_el_registro(base_simulada):
    r = registrar_datos(cedula=CEDULA, nombres="Ana", barrio="Centro", correo="ana@",
                        telefono="t3")
    assert r["ok"] and r["error"] == "CORREO_INVALIDO"


def test_sin_base(monkeypatch):
    monkeypatch.setattr(repositorio, "esta_configurado", lambda: False)
    assert not registrar_datos(cedula=CEDULA, nombres="Ana", barrio="Centro")["ok"]


def test_el_telefono_no_es_argumento_de_la_tool():
    assert set(registrar_ciudadano.args) == {"cedula", "nombres", "correo", "barrio"}


# --- Estado que ve el modelo ----------------------------------------------------

def test_estado_no_registrado():
    assert "NO está registrada" in describir_estado(None)


def test_estado_registrado_no_muestra_la_cedula():
    texto = describir_estado({"cedula": CEDULA, "nombres": "Ana López", "correo": None,
                              "barrio": "Urdesa"})
    assert "YA está registrada" in texto and CEDULA not in texto
    assert "Ana López" in texto and "correo (opcional)" in texto


def test_estado_incompleto():
    texto = describir_estado({"cedula": None, "nombres": "Ana", "correo": None, "barrio": None})
    assert "INCOMPLETO" in texto and "cédula" in texto and "barrio" in texto


# --- Guardrail: un correo no es un enlace ---------------------------------------

@pytest.mark.parametrize("texto", [
    "0915450001, María López, maria.lopez@gmail.com, Urdesa",
    "Correo: pedro_alvarado@hotmail.com",
])
def test_correo_no_se_bloquea_como_url(texto):
    assert _guardrail._check_urls(texto) == (False, "")


def test_enlace_junto_a_un_correo_se_sigue_bloqueando():
    bloqueado, _ = _guardrail._check_urls("mi correo es ana@gmail.com, mira https://estafa.net")
    assert bloqueado


# --- Integración con Postgres ----------------------------------------------------

@pytest.mark.skipif(not _base_disponible(), reason="Postgres local no disponible")
def test_integracion_upsert_por_telefono():
    tel = "593900000777"
    try:
        r1 = registrar_datos(cedula=CEDULA, nombres="Prueba Test", barrio="Centro", telefono=tel)
        r2 = registrar_datos(correo="prueba@test.ec", telefono=tel)
        assert r1["ok"] and r1["nuevo"] and r2["ok"] and not r2["nuevo"]
        assert repositorio.buscar_ciudadano(tel) == {
            "cedula": CEDULA, "nombres": "Prueba Test", "correo": "prueba@test.ec",
            "barrio": "Centro", "cedula_verificada": None}
    finally:
        with repositorio._get_pool().connection() as conn:
            conn.execute(f"delete from {repositorio._esquema_activo()}.ciudadanos "
                         "where telefono = %s", (tel,))
