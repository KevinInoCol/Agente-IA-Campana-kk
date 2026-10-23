"""Firma y parseo del webhook `message.received` de SmartGob Conecta (sin red)."""

import hashlib
import hmac
import json

import pytest

from smartgob_conecta import config, webhook

SECRETO = "whsec_prueba"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(config, "SMARTGOB_CONNECTION_ID", "conn-1")
    monkeypatch.setattr(config, "SMARTGOB_WEBHOOK_SECRET", "")


def _firmar(cuerpo: bytes, t: int) -> str:
    v1 = hmac.new(SECRETO.encode(), str(t).encode() + b"." + cuerpo, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


# --- Firma ------------------------------------------------------------------

def test_firma_correcta(monkeypatch):
    monkeypatch.setattr(config, "SMARTGOB_WEBHOOK_SECRET", SECRETO)
    cuerpo = b'{"event":"message.received"}'
    assert webhook.firma_valida(cuerpo, _firmar(cuerpo, 1_000_000), ahora=1_000_010)


def test_firma_de_otro_cuerpo(monkeypatch):
    monkeypatch.setattr(config, "SMARTGOB_WEBHOOK_SECRET", SECRETO)
    firma = _firmar(b'{"a":1}', 1_000_000)
    assert not webhook.firma_valida(b'{"a":2}', firma, ahora=1_000_000)


def test_firma_vieja_se_rechaza(monkeypatch):
    monkeypatch.setattr(config, "SMARTGOB_WEBHOOK_SECRET", SECRETO)
    cuerpo = b"{}"
    assert not webhook.firma_valida(cuerpo, _firmar(cuerpo, 1_000_000), ahora=1_000_000 + 301)


@pytest.mark.parametrize("cabecera", [None, "", "basura", "t=abc,v1=00", "t=1000000"])
def test_firma_mal_formada(monkeypatch, cabecera):
    monkeypatch.setattr(config, "SMARTGOB_WEBHOOK_SECRET", SECRETO)
    assert not webhook.firma_valida(b"{}", cabecera, ahora=1_000_000)


def test_sin_secreto_rechaza_todo(monkeypatch):
    monkeypatch.setattr(config, "SMARTGOB_ALLOW_UNSIGNED", False)
    assert not webhook.firma_valida(b"{}", None)


def test_sin_secreto_y_modo_desarrollo_acepta(monkeypatch):
    monkeypatch.setattr(config, "SMARTGOB_ALLOW_UNSIGNED", True)
    assert webhook.firma_valida(b"{}", None)


# --- Parseo -----------------------------------------------------------------

def test_formato_real_de_conecta():
    # Estructura vista en el primer mensaje real (2026-10-21); valores inventados.
    payload = {"id": "ev-1", "type": "message.received", "createdAt": "2026-10-21T04:42:00Z",
               "data": {"from": "+593991234567", "text": "hola", "hasMedia": False,
                        "contactId": None, "messageId": "m-9", "messageType": "text",
                        "connectionId": "conn-1", "conversationId": "c-1"}}
    m = webhook.parsear(payload, webhook.tipo_de_evento(payload, None))
    assert (m.telefono, m.texto, m.id_mensaje, m.es_audio) == ("593991234567", "hola", "m-9", False)


def test_evento_de_prueba_real():
    payload = {"id": "ev-2", "type": "webhook.test", "createdAt": "x",
               "data": {"endpointId": "e-1", "message": "prueba"}}
    assert webhook.tipo_de_evento(payload, None) == webhook.EVENTO_PRUEBA
    assert webhook.parsear(payload, webhook.EVENTO_PRUEBA) is None

def _mensaje(**campos):
    base = {"id": "m-1", "connectionId": "conn-1", "direction": "INBOUND",
            "counterpartPhone": "+593991234567", "body": "hola", "messageType": "text"}
    base.update(campos)
    return base


@pytest.mark.parametrize("envoltura", [
    lambda m: {"event": "message.received", "data": {"message": m}},
    lambda m: {"event": "message.received", "data": m},
    lambda m: {"type": "message.received", "message": m},
    lambda m: {"event": "message.received", **m},
])
def test_encuentra_el_mensaje_en_varias_formas(envoltura):
    payload = envoltura(_mensaje())
    m = webhook.parsear(payload, webhook.tipo_de_evento(payload, None))
    assert (m.telefono, m.texto, m.id_mensaje) == ("593991234567", "hola", "m-1")


def test_from_y_text_en_vez_de_counterpartphone_y_body():
    m = webhook.parsear({"data": {"from": "0991234567", "text": "quiero sumarme",
                                  "connectionId": "conn-1"}}, "message.received")
    assert m.telefono == "593991234567"   # 09… se normaliza a 5939…
    assert m.texto == "quiero sumarme"


def test_mensaje_como_texto_plano():
    m = webhook.parsear({"data": {"from": "593991234567", "message": "hola"}},
                        "message.received")
    assert m.texto == "hola"


def test_tipo_por_cabecera_gana():
    assert webhook.tipo_de_evento({"event": "otro"}, "Message.Received") == "message.received"


def test_otro_evento_se_ignora():
    assert webhook.parsear({"data": _mensaje()}, "message.status_changed") is None


def test_saliente_se_ignora():
    assert webhook.parsear({"data": _mensaje(direction="OUTBOUND")}, "message.received") is None


def test_otra_conexion_se_ignora():
    assert webhook.parsear({"data": _mensaje(connectionId="otra")}, "message.received") is None


def test_audio():
    m = webhook.parsear({"data": _mensaje(body="", messageType="audio", mediaFileId="f-1")},
                        "message.received")
    assert m.es_audio and m.media_file_id == "f-1"


def test_imagen_sin_pie_se_ignora():
    assert webhook.parsear({"data": _mensaje(body="", messageType="image")},
                           "message.received") is None


def test_sin_numero_se_ignora():
    assert webhook.parsear({"data": _mensaje(counterpartPhone="")}, "message.received") is None


def test_nombre_del_contacto():
    m = webhook.parsear({"data": _mensaje(contact={"fullName": "Ana Torres"})},
                        "message.received")
    assert m.nombre_perfil == "Ana Torres"


def test_forma_del_payload_no_trae_valores():
    forma = webhook.forma_del_payload({"data": _mensaje()})
    assert "hola" not in json.dumps(forma) and "593991234567" not in json.dumps(forma)
    assert forma["data"]["body"] == "str"


# --- Envío: reintento por contactId -------------------------------------------

def test_parsea_contact_id():
    payload = {"type": "message.received",
               "data": {"from": "+51959000000", "text": "hola", "contactId": "ct-1",
                        "messageId": "m-1", "connectionId": "conn-1"}}
    assert webhook.parsear(payload, "message.received").contact_id == "ct-1"


class _Resp:
    def __init__(self, status, texto=""):
        self.status_code, self.text = status, texto


def _simular_envio(monkeypatch, respuestas):
    import asyncio
    from smartgob_conecta import cliente
    destinos = []

    async def post(destino, texto):
        destinos.append(destino)
        return respuestas.pop(0)

    monkeypatch.setattr(cliente, "_post_mensaje", post)
    return destinos, lambda *a: asyncio.run(cliente.enviar_texto(*a))


def test_numero_rechazado_reintenta_por_contacto(monkeypatch):
    destinos, enviar = _simular_envio(monkeypatch, [_Resp(400, '{"code":"INVALID_PHONE"}'),
                                                    _Resp(202)])
    assert enviar("51959000000", "hola", "ct-1") is True
    assert destinos == [{"to": "51959000000"}, {"contactId": "ct-1"}]


def test_numero_rechazado_sin_contacto_no_reintenta(monkeypatch):
    destinos, enviar = _simular_envio(monkeypatch, [_Resp(400, '{"code":"INVALID_PHONE"}')])
    assert enviar("51959000000", "hola", None) is False and len(destinos) == 1


def test_numero_ecuatoriano_va_directo(monkeypatch):
    destinos, enviar = _simular_envio(monkeypatch, [_Resp(202)])
    assert enviar("593991234567", "hola", "ct-1") is True
    assert destinos == [{"to": "+593991234567"}]
