"""Registro inicial del flujo v2 (F03 a F07): dominio, tools sin LLM y estado del modelo.

Los tests de integración escriben en la base de `DATABASE_URL` y se saltan si no
responde. ⚠️ Con el `.env` actual eso es el Supabase real: correrlos con
`DATABASE_URL` apuntando al Postgres de docker-compose.dev.yml.
"""

import pytest

from dominio import registro as paso
from dominio.correo import normalizar_email
from dominio.identidad import validar_cedula
from dominio.telefono import enmascarar_correo, enmascarar_telefono, normalizar_telefono
from input_guardrails.input_guardrail import _guardrail
from middleware import describir_estado
from simpatizantes import repositorio
from tools import (
    confirmar_nombres,
    confirmar_perfil,
    consultar_cedula,
    guardar_datos_contacto,
)
from tools.registro_tool import (
    guardar_contacto,
    responder_nombres,
    responder_perfil,
    verificar_cedula,
)

# Cédulas sintéticas válidas (provincia 09, módulo 10); no son de nadie.
CEDULA = "0915450001"        # está en fixtures_personas.json (stub)
NO_EXISTE = "0915450019"     # válida pero no está en los fixtures
TEL = "593900000001"


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


@pytest.mark.parametrize("texto,esperado", [
    ("099 123 4567", "593991234567"),
    ("+593 99 123 4567", "593991234567"),
    ("593991234567", "593991234567"),
    ("04 2345678", "59342345678"),
    ("+51 987 654 321", "51987654321"),
    ("12345", None),
    ("0012", None),
    ("hola", None),
])
def test_telefono(texto, esperado):
    assert normalizar_telefono(texto) == esperado


def test_mascaras():
    assert enmascarar_telefono("593991234567") == "*********567"
    assert enmascarar_correo("carlos.mora@gmail.com") == "c***@gmail.com"


def test_orden_de_pasos():
    r = {}
    assert paso.siguiente_paso(None) == paso.CEDULA
    assert paso.siguiente_paso(r) == paso.CEDULA
    r.update(cedula=CEDULA, nombres="MORA PEREZ ANA LUCIA")
    assert paso.siguiente_paso(r) == paso.CONFIRMAR_NOMBRES
    r["nombres_confirmados"] = True
    assert paso.siguiente_paso(r) == paso.CORREO
    r["correo"] = "a@b.ec"
    assert paso.siguiente_paso(r) == paso.TELEFONO
    r["telefono_contacto"] = "593991234567"
    assert paso.siguiente_paso(r) == paso.BARRIO
    r["barrio"] = "Urdesa"
    assert paso.siguiente_paso(r) == paso.REVISAR_PERFIL
    r["registro_confirmado_en"] = "2026-10-24"
    assert paso.siguiente_paso(r) == paso.COMPLETO


def test_nombres_de_la_dinardap():
    # La fuente entrega "APELLIDOS NOMBRES".
    assert paso.nombre_de_pila("MORA PEREZ ANA LUCIA") == "Ana"
    assert paso.nombre_de_pila("MORA ANA") == "Mora"
    assert paso.nombre_para_mostrar("MORA PEREZ ANA LUCIA") == "Mora Perez Ana Lucia"


def test_sin_telefono(repo, fuente):
    assert verificar_cedula(CEDULA, None)["error"] == "SIN_TELEFONO"


# --- F03/F04 cédula y nombres ---------------------------------------------------

@pytest.fixture
def acepto(repo):
    return repo


def test_cedula_existe_devuelve_nombres_sin_confirmar(acepto, fuente):
    r = verificar_cedula("091-545-0001", TEL)
    assert r["ok"] and r["nombre_completo"] == "Prueba Sintetica Maria Fernanda"
    assert r["siguiente_paso"] == paso.CONFIRMAR_NOMBRES
    fila = acepto.ciudadanos[TEL]
    assert fila["cedula"] == CEDULA and fila["cedula_verificada"] is True
    assert fila["nombres_confirmados"] is False


def test_cedula_invalida_no_consulta(acepto, fuente, monkeypatch):
    from integraciones.persona import adaptador
    monkeypatch.setattr(adaptador, "consultar_persona", lambda *a, **k: pytest.fail("red"))
    assert verificar_cedula("0915450002", TEL)["error"] == "CEDULA_INVALIDA"


def test_cedula_no_existe_no_se_guarda(acepto, fuente):
    r = verificar_cedula(NO_EXISTE, TEL)
    assert not r["ok"] and r["error"] == "CEDULA_NO_EXISTE"
    assert TEL not in acepto.ciudadanos


def test_dinardap_apagada_no_registra(acepto, fuente):
    fuente("ninguna")
    r = verificar_cedula(CEDULA, TEL)
    assert r["error"] == "SERVICIO_NO_DISPONIBLE" and TEL not in acepto.ciudadanos


def test_dinardap_caida_no_registra(acepto, fuente, monkeypatch):
    import httpx
    from integraciones.persona import adaptador
    fuente("ventanilla")
    monkeypatch.setattr(adaptador, "VENTANILLA_USER", "u")
    monkeypatch.setattr(adaptador, "VENTANILLA_PASSWORD", "p")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("x")))
    r = verificar_cedula(CEDULA, TEL)
    assert r["error"] == "SERVICIO_NO_DISPONIBLE" and TEL not in acepto.ciudadanos


def test_cedula_vacia_usa_la_guardada_del_guion_v1(acepto, fuente):
    acepto.ciudadanos[TEL] = {"cedula": CEDULA, "nombres": None}     # registro viejo
    r = verificar_cedula("", TEL)
    assert r["ok"] and r["nombre_completo"]


def test_confirmar_nombres(acepto, fuente):
    verificar_cedula(CEDULA, TEL)
    r = responder_nombres(True, TEL)
    assert r["ok"] and r["siguiente_paso"] == paso.CORREO and r["nombre"] == "Maria"


def test_revisar_cedula_descarta_cedula_y_nombres(acepto, fuente):
    verificar_cedula(CEDULA, TEL)
    r = responder_nombres(False, TEL)
    assert r["siguiente_paso"] == paso.CEDULA
    assert acepto.ciudadanos[TEL]["cedula"] is None and acepto.ciudadanos[TEL]["nombres"] is None


def test_confirmar_nombres_sin_consulta(acepto):
    assert responder_nombres(True, TEL)["error"] == "SIN_NOMBRES_PENDIENTES"


# --- F05/F06 contacto y resumen ---------------------------------------------------

@pytest.fixture
def identificado(acepto, fuente):
    verificar_cedula(CEDULA, TEL)
    responder_nombres(True, TEL)
    return acepto


def test_contacto_por_partes_y_en_orden(identificado):
    r = guardar_contacto(TEL, correo="Ana@Gmail.com", barrio="Urdesa")
    assert r["ok"] and r["siguiente_paso"] == paso.TELEFONO
    r = guardar_contacto(TEL, telefono_contacto="099 123 4567")
    assert r["siguiente_paso"] == paso.REVISAR_PERFIL
    assert r["resumen"] == {"nombre_completo": "Prueba Sintetica Maria Fernanda",
                            "correo_enmascarado": "a***@gmail.com",
                            "telefono_enmascarado": "*********567", "barrio": "Urdesa"}


def test_usar_el_numero_de_whatsapp(identificado):
    guardar_contacto(TEL, usar_whatsapp=True)
    assert identificado.ciudadanos[TEL]["telefono_contacto"] == TEL


def test_dato_invalido_conserva_los_validos(identificado):
    r = guardar_contacto(TEL, correo="sin arroba", telefono_contacto="12", barrio="Centro")
    assert r["ok"] and "CORREO_INVALIDO" in r["error"] and "TELEFONO_INVALIDO" in r["error"]
    assert identificado.ciudadanos[TEL]["barrio"] == "Centro"
    assert r["siguiente_paso"] == paso.CORREO


def test_solo_un_dato_invalido(identificado):
    r = guardar_contacto(TEL, correo="mal")
    assert not r["ok"] and r["error"] == "CORREO_INVALIDO"


@pytest.fixture
def perfil_completo(identificado):
    guardar_contacto(TEL, correo="ana@gmail.com", telefono_contacto="0991234567",
                     barrio="Urdesa")
    return identificado


def test_confirmar_perfil(perfil_completo):
    r = responder_perfil(True, TEL)
    assert r["ok"] and r["siguiente_paso"] == paso.COMPLETO and r["nombre"] == "Maria"


def test_confirmar_perfil_incompleto(identificado):
    assert responder_perfil(True, TEL)["error"] == "PERFIL_INCOMPLETO"


def test_corregir_un_dato_pide_revisar_el_resumen_otra_vez(perfil_completo):
    responder_perfil(True, TEL)
    r = guardar_contacto(TEL, barrio="Guasmo")
    assert r["siguiente_paso"] == paso.REVISAR_PERFIL


def test_repetir_el_mismo_dato_no_deshace_la_confirmacion(perfil_completo):
    responder_perfil(True, TEL)
    assert guardar_contacto(TEL, barrio="Urdesa")["siguiente_paso"] == paso.COMPLETO


def test_cancelar_registro_borra_los_datos(perfil_completo):
    r = responder_perfil(False, TEL)
    assert r["ok"] and r["registro_cancelado"] and TEL not in perfil_completo.ciudadanos


def test_la_cedula_no_vuelve_al_modelo_ni_a_los_logs(perfil_completo, caplog):
    with caplog.at_level("DEBUG"):
        r = verificar_cedula(CEDULA, TEL)
        verificar_cedula(NO_EXISTE, TEL)
    assert CEDULA not in str(r) and CEDULA not in caplog.text and NO_EXISTE not in caplog.text


def test_el_telefono_de_whatsapp_no_es_argumento_de_ninguna_tool():
    assert set(consultar_cedula.args) == {"cedula"}
    assert set(confirmar_nombres.args) == {"correctos"}
    assert set(guardar_datos_contacto.args) == {"correo", "telefono", "usar_whatsapp", "barrio"}
    assert set(confirmar_perfil.args) == {"confirma"}


# --- Estado que ve el modelo ----------------------------------------------------

def test_estado_sin_registro():
    assert "paso_pendiente: CEDULA" in describir_estado(None)


def test_estado_no_muestra_la_cedula_ni_datos_completos():
    texto = describir_estado({"cedula": CEDULA,
                              "nombres": "MORA PEREZ ANA LUCIA", "nombres_confirmados": True,
                              "correo": "ana@gmail.com", "telefono_contacto": "593991234567",
                              "barrio": "Urdesa"})
    assert "REVISAR_PERFIL" in texto and CEDULA not in texto
    assert "ana@gmail.com" not in texto and "a***@gmail.com" in texto
    assert "593991234567" not in texto and "nombre: Ana" in texto


def test_estado_cedula_vieja_sin_consultar():
    texto = describir_estado({"cedula": CEDULA})
    assert "cedula vacía" in texto and CEDULA not in texto


def test_estado_con_borrador():
    texto = describir_estado({"cedula": CEDULA}, {"id": 1, "ayuda": "Voluntaria"})
    assert "Voluntaria" in texto and "borrador" in texto


def test_estado_sin_consultar():
    assert "problema temporal" in describir_estado(None, consultado=False)


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

def _base_disponible() -> bool:
    if not repositorio.esta_configurado():
        return False
    try:
        repositorio._get_pool()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _base_disponible(), reason="Postgres no disponible")
def test_integracion_registro_completo(fuente):
    tel = "593900000777"
    try:
        assert verificar_cedula(CEDULA, tel, nombre_perfil="Prueba")["ok"]
        assert responder_nombres(True, tel)["ok"]
        assert guardar_contacto(tel, correo="prueba@test.ec", usar_whatsapp=True,
                                barrio="Centro")["siguiente_paso"] == paso.REVISAR_PERFIL
        assert responder_perfil(True, tel)["siguiente_paso"] == paso.COMPLETO
        fila = repositorio.buscar_ciudadano(tel)
        assert fila["cedula"] == CEDULA and fila["telefono_contacto"] == tel
        assert fila["registro_confirmado_en"]
        assert responder_perfil(False, tel)["registro_cancelado"]
        assert repositorio.buscar_ciudadano(tel) is None
    finally:
        repositorio.borrar_ciudadano(tel)
