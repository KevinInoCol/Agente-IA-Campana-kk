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
    # Los textos del flujo v2 del cliente (Flujo_Agente_Juan_Jose.docx) están en <guion>.
    for seccion in ("BIENVENIDA", "PEDIR_CEDULA", "CEDULA_INVALIDA", "CEDULA_NO_EXISTE",
                    "CONSULTA_NO_DISPONIBLE", "CONFIRMAR_NOMBRES", "PEDIR_CORREO",
                    "PEDIR_TELEFONO", "PEDIR_BARRIO", "RESUMEN_PERFIL", "REGISTRO_CANCELADO",
                    "NO_SE_PUDO_GUARDAR", "SALIR", "MENU_PRINCIPAL", "QUIEN_ES", "FAMILIA",
                    "FORMACION", "VALORES_1", "VALORES_2", "PROYECTOS", "SUMARME_AUTORIZACION",
                    "SUMARME_IDEA", "RESUMEN_COLABORACION", "SUMARME_CIERRE", "SUMARME_FALLO",
                    "SUMARME_INCIERTO", "OPCION_NO_DISPONIBLE", "GRACIAS", "FUERA_DE_ALCANCE"):
        assert re.search(rf"^{seccion}$", SP, re.M), seccion


def _texto(seccion):
    return SP.split(f"\n{seccion}\n")[1].split("\n\n")[0]


def test_menu_principal_tiene_las_6_opciones():
    opciones = re.findall(r"^\d\. ", _texto("MENU_PRINCIPAL"), re.M)
    assert len(opciones) == 6 and "*Formación*" in _texto("MENU_PRINCIPAL")


def test_el_cierre_no_promete_contacto():
    assert "muy pronto" not in SP.split("<guion>")[1].split("</guion>")[0]


def test_cada_paso_del_registro_tiene_su_texto():
    from dominio import registro as paso
    tabla = SP.split("\n<registro>\n")[1].split("\n</registro>\n")[0]
    for nombre in (paso.CEDULA, paso.CONFIRMAR_NOMBRES, paso.CORREO,
                   paso.TELEFONO, paso.BARRIO, paso.REVISAR_PERFIL, paso.COMPLETO):
        assert re.search(rf"^\| {nombre} ", tabla, re.M), nombre


def test_errores_de_las_tools_estan_en_el_prompt():
    from tools import colaboracion_tool as c, registro_tool as r
    for codigo in (r.CEDULA_INVALIDA, r.CEDULA_NO_EXISTE,
                   r.SERVICIO_NO_DISPONIBLE, r.CORREO_INVALIDO, r.TELEFONO_INVALIDO,
                   r.NO_SE_PUDO_GUARDAR, r.SIN_TELEFONO,
                   r.SIN_NOMBRES_PENDIENTES, r.PERFIL_INCOMPLETO, c.FALLO, c.RESULTADO_INCIERTO, c.SIN_BORRADOR):
        assert codigo in SP, codigo


def test_sin_aviso_de_privacidad():
    # El cliente descartó el paso F02 (no hay enlace de privacidad).
    assert "aceptar_terminos" not in SP and "{enlace_privacidad}" not in SP
