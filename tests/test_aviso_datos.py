"""El aviso de protección de datos (BIENVENIDA) se muestra SIEMPRE antes de la cédula."""

from types import SimpleNamespace

import yaml

from agent import BASE_DIR
from langchain.agents.middleware import ModelResponse
from langchain.messages import AIMessage, HumanMessage, SystemMessage
from middleware import estado_registro as er

TEL = "593963264844"


class _Req:
    def __init__(self, mensajes):
        self.messages = mensajes
        self.system_message = SystemMessage(content="prompt")
        self.runtime = SimpleNamespace(context=SimpleNamespace(telefono=TEL))
        self.visto = None

    def override(self, system_message):
        self.visto = system_message
        return self


def _correr(mensajes, respuesta_modelo, paso_pendiente, monkeypatch):
    monkeypatch.setattr(er, "_consultar",
                        lambda tel: ("<estado_del_registro>\nx\n</estado_del_registro>",
                                     paso_pendiente))
    req = _Req(mensajes)
    out = er.EstadoRegistroMiddleware().wrap_model_call(
        req, lambda r: ModelResponse(result=[respuesta_modelo]))
    return out.result[0], req


def test_texto_igual_al_del_prompt():
    prompt = yaml.safe_load((BASE_DIR / "prompt" / "prompt.yaml").read_text("utf-8"))
    assert er.BIENVENIDA in prompt["system_prompt"]


def test_persona_que_vuelve_sin_aviso_recibe_bienvenida(monkeypatch):
    # Caso real: escribió ayer, hoy dice "hola" y el modelo solo pidió la cédula.
    historial = [HumanMessage("0992499710"), AIMessage("Mmm, no pude validar esa cédula"),
                 HumanMessage("hola")]
    msg, req = _correr(historial, AIMessage("¡Hola! 👋 ¿Cuál es tu número de cédula? 🪪"),
                       "CEDULA", monkeypatch)
    assert msg.text == er.BIENVENIDA
    assert "aviso_de_datos: NO mostrado" in req.visto.content_blocks[-1]["text"]


def test_no_consulta_la_cedula_antes_del_aviso(monkeypatch):
    llamada = AIMessage("", tool_calls=[{"name": "consultar_cedula",
                                         "args": {"cedula": "0910978444"}, "id": "1"}])
    msg, _ = _correr([HumanMessage("0910978444")], llamada, "CEDULA", monkeypatch)
    assert msg.text == er.BIENVENIDA and not msg.tool_calls


def test_con_aviso_ya_mostrado_no_se_toca(monkeypatch):
    llamada = AIMessage("", tool_calls=[{"name": "consultar_cedula",
                                         "args": {"cedula": "0910978444"}, "id": "1"}])
    msg, req = _correr([AIMessage(er.BIENVENIDA), HumanMessage("0910978444")], llamada,
                       "CEDULA", monkeypatch)
    assert msg is llamada
    assert "aviso_de_datos" not in req.visto.content_blocks[-1]["text"]


def test_modelo_que_si_envia_bienvenida_no_se_toca(monkeypatch):
    propia = AIMessage(er.BIENVENIDA)
    msg, _ = _correr([HumanMessage("hola")], propia, "CEDULA", monkeypatch)
    assert msg is propia


def test_registro_completo_no_recibe_bienvenida(monkeypatch):
    menu = AIMessage("¡Hola de nuevo, Ana! 👋")
    msg, _ = _correr([HumanMessage("hola")], menu, "COMPLETO", monkeypatch)
    assert msg is menu
