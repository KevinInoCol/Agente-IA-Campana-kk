"""Formato del prompt (skill agent-prompt-yaml-format) y coherencia con las tools."""

import re
from pathlib import Path

import yaml

from agent import TOOLS_CABLEADAS

PROMPT = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "prompt" / "prompt.yaml").read_text(encoding="utf-8")
)
SP = PROMPT["system_prompt"]
ABIERTOS = re.findall(r"^[ \t]*<([A-Za-z_]+)>[ \t]*$", SP, re.M)
CERRADOS = re.findall(r"^[ \t]*</([A-Za-z_]+)>[ \t]*$", SP, re.M)


def test_metadata():
    for clave in ("name", "version", "description", "language", "author"):
        assert PROMPT.get(clave), clave
    assert PROMPT["author"] == "kevin.inofuente.colque.27@gmail.com"


def test_todos_los_tags_cierran():
    assert set(ABIERTOS) == set(CERRADOS)
    assert len(ABIERTOS) == len(CERRADOS)


def test_convencion_unica_minuscula():
    assert all(t.islower() for t in ABIERTOS), ABIERTOS


def test_tools_del_prompt_existen():
    nombres = {t.name for t in TOOLS_CABLEADAS}
    bloque = SP.split("<herramientas>")[1].split("</herramientas>")[0]
    citadas = set(re.findall(r"^([a-z_]+)\(", bloque, re.M))
    assert citadas == nombres


def test_guion_completo():
    # Las secciones del guion v1 del cliente (FlujoTTRBOT v1) tienen su texto en <guion>.
    for seccion in ("BIENVENIDA_REGISTRO", "MENU_PRINCIPAL", "QUIEN_ES", "FAMILIA", "VALORES_1",
                    "VALORES_2", "FORMACION", "PROYECTOS", "SUMARME_AYUDA", "SUMARME_CIERRE",
                    "FALLBACK", "GRACIAS", "FUERA_DE_ALCANCE"):
        assert re.search(rf"^{seccion}$", SP, re.M), seccion


def test_quiero_sumarme_no_vuelve_a_pedir_nombre_ni_barrio():
    # Pasos 1 y 2 en rojo en el guion v1: el cliente pidió omitirlos.
    assert "SUMARME_NOMBRE" not in SP and "SUMARME_BARRIO" not in SP


def test_menu_principal_tiene_las_6_opciones():
    linea = SP.split("MENU_PRINCIPAL\n")[1].split("\n")[1]
    assert linea.startswith("Opciones:") and len(linea.split("·")) == 6 and "Formación" in linea
