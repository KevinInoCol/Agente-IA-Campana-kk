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
    # Los textos del flujo v4 del cliente (TOTOBOT.docx) están en <guion>.
    for seccion in ("BIENVENIDA", "PEDIR_CEDULA", "CEDULA_INVALIDA", "CONSULTA_NO_DISPONIBLE",
                    "PEDIR_CORREO", "CORREO_INVALIDO", "PEDIR_CELULAR", "CELULAR_INVALIDO",
                    "PEDIR_BARRIO", "BARRIO_INVALIDO", "REGISTRO_COMPLETO", "MENU_PRINCIPAL",
                    "REGRESO", "NO_SE_PUDO_GUARDAR", "SALIR", "BIOGRAFIA", "PROPUESTAS",
                    "INVITACION", "IDEA_CORTA", "CONFIRMAR_IDEA", "MODIFICAR_IDEA",
                    "OPCION_INVALIDA_IDEA", "IDEA_ENVIADA", "ENVIO_FALLIDO", "ENVIO_INCIERTO",
                    "OPCION_NO_DISPONIBLE", "SOLO_TEXTO", "GRACIAS", "FUERA_DE_ALCANCE"):
        assert re.search(rf"^{seccion}$", SP, re.M), seccion


def _bloque(seccion):
    """Texto de una sección del guion, hasta la siguiente sección en MAYÚSCULAS."""
    resto = SP.split(f"\n{seccion}\n", 1)[1]
    return re.split(r"\n[A-Z_]+\n|\n</guion>", resto, maxsplit=1)[0]


def test_menu_principal_tiene_las_4_opciones():
    for menu in ("REGISTRO_COMPLETO", "MENU_PRINCIPAL", "REGRESO"):
        texto = _bloque(menu)
        assert len(re.findall(r"^[1-4]\ufe0f\u20e3 ", texto, re.M)) == 4, menu
        assert "Quiero hacer una propuesta" in texto


def test_bienvenida_pide_consentimiento():
    assert "aceptas que usemos tu información" in _bloque("BIENVENIDA")


def test_cierre_recuerda_la_fecha_de_votacion():
    assert "29 de noviembre" in _bloque("IDEA_ENVIADA")


def test_cada_paso_del_registro_tiene_su_texto():
    from dominio import registro as paso
    tabla = SP.split("\n<registro>\n")[1].split("\n</registro>\n")[0]
    for nombre in (paso.CEDULA, paso.CORREO, paso.TELEFONO, paso.BARRIO, paso.COMPLETO):
        assert re.search(rf"^\| {nombre} ", tabla, re.M), nombre


def test_errores_de_las_tools_estan_en_el_prompt():
    from tools import colaboracion_tool as c, registro_tool as r
    for codigo in (r.CEDULA_INVALIDA, r.CEDULA_NO_EXISTE, r.SERVICIO_NO_DISPONIBLE,
                   r.CORREO_INVALIDO, r.TELEFONO_INVALIDO, r.BARRIO_INVALIDO,
                   r.NO_SE_PUDO_GUARDAR, r.SIN_TELEFONO, c.IDEA_CORTA, c.SIN_IDEA,
                   c.PERFIL_INCOMPLETO, c.FALLO, c.RESULTADO_INCIERTO, c.SIN_BORRADOR):
        assert codigo in SP, codigo


def test_sin_pasos_del_flujo_v2():
    for viejo in ("confirmar_nombres", "confirmar_perfil", "RESUMEN_PERFIL",
                  "CONFIRMAR_NOMBRES", "REVISAR_PERFIL", "{enlace_privacidad}"):
        assert viejo not in SP, viejo
