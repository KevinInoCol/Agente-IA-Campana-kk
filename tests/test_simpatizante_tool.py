"""Tool `guardar_simpatizante` y su lógica, sin LLM.

Los tests marcados `integracion` escriben en la base de `DATABASE_URL` (en local, el
Postgres de docker-compose.dev.yml) y se saltan si no responde.
"""

import pytest

from simpatizantes import apps_script, repositorio
from tools.simpatizante_tool import guardar_simpatizante, registrar_simpatizante


def _base_disponible() -> bool:
    if not repositorio.esta_configurado():
        return False
    try:
        repositorio._get_pool()
        return True
    except Exception:  # noqa: BLE001
        return False


def test_faltan_datos(monkeypatch):
    r = registrar_simpatizante(nombre="Ana", barrio="  ", ayuda="")
    assert r["ok"] is False
    assert "barrio" in r["error"] and "ayuda" in r["error"]


def test_sin_destinos(monkeypatch):
    monkeypatch.setattr(repositorio, "esta_configurado", lambda: False)
    monkeypatch.setattr(apps_script, "esta_configurado", lambda: False)
    r = registrar_simpatizante(nombre="Ana", barrio="Urdesa", ayuda="Voluntaria")
    assert r["ok"] is False


def test_apps_script_falla_pero_postgres_guarda(monkeypatch):
    monkeypatch.setattr(repositorio, "esta_configurado", lambda: True)
    monkeypatch.setattr(repositorio, "guardar", lambda s: 7)
    monkeypatch.setattr(apps_script, "esta_configurado", lambda: True)

    def _falla(*a, **k):
        raise RuntimeError("caído")

    monkeypatch.setattr(apps_script, "enviar", _falla)
    r = registrar_simpatizante(nombre="Ana", barrio="Urdesa", ayuda="Voluntaria")
    assert r["ok"] is True
    assert r["destinos"]["postgres"] == "ok (id 7)"
    assert r["destinos"]["apps_script"].startswith("error")


def test_todo_falla(monkeypatch):
    monkeypatch.setattr(repositorio, "esta_configurado", lambda: True)

    def _falla(*a, **k):
        raise RuntimeError("caído")

    monkeypatch.setattr(repositorio, "guardar", _falla)
    monkeypatch.setattr(apps_script, "esta_configurado", lambda: False)
    r = registrar_simpatizante(nombre="Ana", barrio="Urdesa", ayuda="Voluntaria")
    assert r["ok"] is False


def test_el_telefono_no_es_argumento_de_la_tool():
    # Lo que ve el modelo: solo nombre, barrio y ayuda. `runtime` es inyectado.
    assert set(guardar_simpatizante.args) == {"nombre", "barrio", "ayuda"}


@pytest.mark.skipif(not _base_disponible(), reason="Postgres local no disponible")
def test_integracion_guarda_en_postgres():
    r = registrar_simpatizante(nombre="Prueba Test", barrio="Centro", ayuda="Test automático",
                               telefono="593900000000", canal="cli")
    assert r["ok"] is True, r
    id_ = int(r["destinos"]["postgres"].split("id ")[1].rstrip(")"))
    with repositorio._get_pool().connection() as conn:
        fila = conn.execute(
            "select current_schema(), telefono, nombre, barrio from "
            + f"{repositorio._esquema_activo()}.simpatizantes where id = %s", (id_,)
        ).fetchone()
        conn.execute(f"delete from {repositorio._esquema_activo()}.simpatizantes where id = %s",
                     (id_,))
    assert fila[1:] == ("593900000000", "Prueba Test", "Centro")
