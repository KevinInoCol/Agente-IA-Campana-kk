"""'Quiero sumarme' del flujo v2 (F09 a F13): borrador, confirmación idempotente y fallos."""

from datetime import datetime, timezone

import pytest
from psycopg_pool import PoolTimeout

from simpatizantes import apps_script, repositorio
from tools import confirmar_colaboracion, preparar_colaboracion
from tools.colaboracion_tool import confirmar, preparar

TEL = "593900000002"


@pytest.fixture
def registrado(repo):
    repo.ciudadanos[TEL] = {
        "cedula": "0915450001", "nombres": "MORA PEREZ ANA LUCIA",
        "nombres_confirmados": True, "correo": "ana@gmail.com",
        "telefono_contacto": "593991234567", "barrio": "Urdesa",
        "registro_confirmado_en": datetime.now(timezone.utc),
    }
    return repo


def test_sin_perfil_completo_no_prepara(repo):
    repo.ciudadanos[TEL] = {"barrio": "Urdesa"}
    r = preparar("Quiero ser voluntaria", TEL)
    assert r["error"] == "PERFIL_INCOMPLETO" and r["siguiente_paso"] == "CEDULA"


def test_sin_idea(registrado):
    assert preparar("   ", TEL)["error"] == "SIN_IDEA"


def test_preparar_devuelve_el_resumen_y_no_registra(registrado):
    r = preparar("Quiero ser voluntaria", TEL)
    assert r["ok"] and r["resumen"] == {
        "nombre_completo": "Mora Perez Ana Lucia", "correo_enmascarado": "a***@gmail.com",
        "telefono_enmascarado": "*********567", "barrio": "Urdesa",
        "ayuda": "Quiero ser voluntaria"}
    assert [c["estado"] for c in registrado.colaboraciones] == ["borrador"]


def test_corregir_la_idea_reemplaza_el_borrador(registrado):
    preparar("Idea uno", TEL)
    preparar("Idea dos", TEL)
    assert len(registrado.colaboraciones) == 1
    assert registrado.colaboraciones[0]["ayuda"] == "Idea dos"


def test_confirmar_registra_con_los_datos_revisados(registrado):
    preparar("Voluntaria", TEL)
    r = confirmar(True, TEL)
    assert r["ok"] and r["nombre"] == "Ana"
    c = registrado.colaboraciones[0]
    assert c["estado"] == "confirmada" and c["correo"] == "ana@gmail.com"
    assert c["telefono_contacto"] == "593991234567"


def test_confirmar_dos_veces_no_duplica(registrado):
    preparar("Voluntaria", TEL)
    confirmar(True, TEL)
    r = confirmar(True, TEL)
    assert r["ok"] and r["ya_registrada"]
    assert len(registrado.colaboraciones) == 1


def test_confirmar_sin_borrador(registrado):
    assert confirmar(True, TEL)["error"] == "SIN_BORRADOR"


def test_cancelar(registrado):
    preparar("Voluntaria", TEL)
    r = confirmar(False, TEL)
    assert r["ok"] and r["cancelada"]
    assert registrado.colaboraciones[0]["estado"] == "cancelada"
    assert confirmar(True, TEL)["error"] == "SIN_BORRADOR"


def test_una_nueva_colaboracion_pide_una_idea_nueva(registrado):
    preparar("Idea uno", TEL)
    confirmar(True, TEL)
    preparar("Idea dos", TEL)
    assert [c["estado"] for c in registrado.colaboraciones] == ["confirmada", "borrador"]


def test_sin_conexion_es_fallo(registrado):
    preparar("Voluntaria", TEL)
    registrado.fallar = PoolTimeout("sin conexión")
    assert confirmar(True, TEL)["error"] == "FALLO"


def test_error_en_medio_es_incierto_y_comprobar_no_duplica(registrado):
    preparar("Voluntaria", TEL)
    registrado.fallar = RuntimeError("se cortó")
    assert confirmar(True, TEL)["error"] == "RESULTADO_INCIERTO"
    registrado.fallar = None
    r = confirmar(True, TEL)                  # "Comprobar estado"
    assert r["ok"] and len(registrado.colaboraciones) == 1


def test_apps_script_falla_pero_queda_registrada(registrado, monkeypatch):
    monkeypatch.setattr(apps_script, "esta_configurado", lambda: True)
    monkeypatch.setattr(apps_script, "enviar",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("caído")))
    preparar("Voluntaria", TEL)
    assert confirmar(True, TEL)["ok"]


def test_confirmar_tambien_confirma_el_perfil_revisado(registrado):
    registrado.ciudadanos[TEL]["registro_confirmado_en"] = None    # corrigió en F11
    preparar("Voluntaria", TEL)
    confirmar(True, TEL)
    assert registrado.ciudadanos[TEL]["registro_confirmado_en"]


def test_el_telefono_no_es_argumento_de_las_tools():
    assert set(preparar_colaboracion.args) == {"ayuda"}
    assert set(confirmar_colaboracion.args) == {"confirma"}


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
def test_integracion_borrador_y_confirmacion():
    tel = "593900000778"
    esquema = repositorio._esquema_activo()
    try:
        repositorio.actualizar_ciudadano(
            tel, cedula="0915450001", nombres="PRUEBA TEST ANA",
            nombres_confirmados=True, correo="p@test.ec", telefono_contacto=tel,
            barrio="Centro", registro_confirmado_en=datetime.now(timezone.utc))
        assert preparar("Uno", tel)["ok"] and preparar("Dos", tel)["ok"]
        assert repositorio.obtener_borrador(tel)["ayuda"] == "Dos"
        assert confirmar(True, tel)["ok"]
        assert confirmar(True, tel)["ya_registrada"]
        with repositorio._get_pool().connection() as conn:
            filas = conn.execute(f"select estado, correo from {esquema}.simpatizantes "
                                 "where telefono = %s", (tel,)).fetchall()
        assert filas == [("confirmada", "p@test.ec")]
    finally:
        with repositorio._get_pool().connection() as conn:
            conn.execute(f"delete from {esquema}.simpatizantes where telefono = %s", (tel,))
        repositorio.borrar_ciudadano(tel)
