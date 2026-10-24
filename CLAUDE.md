# Agente WhatsApp · Juan José "Toto" Reyes Baquerizo

Asistente de WhatsApp de la campaña **"Toto Reyes · Concejal"** (Guayaquil). Hecho
con LangChain v1 (`create_agent`) + LangGraph sobre FastAPI. El canal es la API de
**SmartGob Conecta** (plataforma de Origami que corre Evolution API por debajo), por
indicación del cliente (2026-10-21).

Hace tres cosas (flujo v2, `Flujo_Agente_Juan_Jose.docx`, 2026-10-24):
1. **Registro inicial obligatorio antes del menú**: cédula consultada en la **DINARDAP**, que devuelve los nombres → la persona los
   confirma → correo, teléfono de contacto y barrio → resumen y confirmación. Tabla
   `ciudadanos`. El middleware `EstadoRegistro` le dice al modelo, en cada turno, en
   qué paso está la persona (`paso_pendiente`).
2. **Presenta al candidato** con los textos aprobados: quién es, su familia, sus
   valores, su formación y sus proyectos, con una tabla de navegación entre menús.
3. **Registra colaboraciones** con "Quiero sumarme": autorización → idea (borrador)
   → resumen → confirmación expresa. Tabla `simpatizantes` (estado borrador /
   confirmada / cancelada). **Registro y colaboraciones son los datos que le importan
   al cliente.**

## Fuente de verdad del contenido

En `../` (carpeta del cliente):

| Documento | Uso |
|---|---|
| `Flujo_Agente_Juan_Jose.docx` (2026-10-24) | **Fuente de verdad (flujo v2, pasos F01 a F13).** Textos casi literales en `<guion>` del prompt. Su página 10 lista puntos "por validar"; ver abajo cómo quedaron. |
| `FlujoTTRBOT v1.docx` (2026-10-22) | Guion v1, reemplazado por el v2. |
| `Datos/BOT_x5f_WHATSAPP_…_FINAL.docx/.pdf` | Guion anterior, reemplazado por el v1. |
| `TOTO REYES2.pdf` (manual de marca) | Solo el cargo (Concejal) y las frases de campaña. |
| `AYUDA MEMORIA INTEGRAL_….docx/.pdf` | **No se usa.** Es una plantilla con las secciones vacías, y los valores que trae son de otro perfil ("formación médica", "Medicina"). |
| `postman-ap.zip`, `key-conexion.txt` | API y credencial de **SmartGob Conecta**: es el canal del agente. La key ya está en el `.env` (`SMARTGOB_API_KEY`). |

Si el cliente cambia un texto del bot, se cambia en `prompt/prompt.yaml` → `<guion>`,
y se sube la `version` del prompt.

## Repositorio y despliegue

- Repo **privado**: `https://github.com/KevinInoCol/Agente-IA-Campana-kk`
  (cuenta personal KevinInoCol, remote `origin`, rama `main`). En esta máquina la cuenta
  activa de `gh` es otra (kaic-ai): para operar con este repo usar
  `GH_TOKEN="$(gh auth token --user KevinInoCol)" gh ...`.
- **Backend** en EasyPanel con el `Dockerfile` de la raíz. Las variables de entorno van
  en el panel (ver `.env.example`); el `.env` no se sube ni entra en la imagen.
- Al desplegar, editar la URL del webhook en SmartGob Conecta (Administración →
  Integraciones → Webhooks) para que apunte al dominio de EasyPanel. El secreto de firma
  no cambia.

## Comandos

```bash
conda activate toto-reyes                               # Python 3.11, requirements-dev.txt
docker compose -p toto-reyes-dev -f docker-compose.dev.yml up -d   # Postgres :55432 + Redis :56379 (desechables)
python -m pytest -q                                     # 143 tests (OJO: los 2 de integración usan la base del .env, hoy el Supabase real: correrlos con DATABASE_URL local)
python rag/rag.py --dry-run                            # revisa la Ayuda Memoria sin gastar tokens ni tocar Qdrant
python main.py --telefono 593991234567                  # CLI
uvicorn main_conecta:app --reload --port 8000           # canal WhatsApp (webhook de SmartGob Conecta)
```

## Estructura

```
agent.py                 orquestador: LLM + tools + prompt + guardrail + memoria + Langfuse
agent_context.py         ConversacionContext (teléfono y pushName, ocultos al LLM)
main.py                  CLI
main_conecta.py          FastAPI: webhook de Conecta (firma HMAC) -> debounce -> agente -> POST /messages
model_config/            parámetros del LLM (gpt-5.6-sol, reasoning_effort none; desde 2026-10-24)
prompt/prompt.yaml       system prompt en formato TAGS, con el guion del cliente
tools/                   registro_tool (5 tools, F02-F06) + colaboracion_tool (2 tools, F09-F12); buscar_conocimiento (SIN cablear)
middleware/              EstadoRegistro: agrega <terminos> y <estado_del_registro> al system prompt en cada llamada
dominio/                 cédula y correo (de MAD), teléfono, orden del registro
integraciones/persona/   consulta al registro civil (DINARDAP vía ventanilla)        (de MAD)
rag/rag.py               ingesta de la Ayuda Memoria a Qdrant (rag/data/, no versionado) (de MAD)
simpatizantes/           repositorio de ciudadanos y simpatizantes: Postgres + Apps Script (opcional)
smartgob_conecta/        config, cliente (POST /messages, audios) y firma/parser del webhook
transcripcion.py         notas de voz -> texto (whisper-1)
conversation_history/    checkpointer PostgresSaver en el esquema DB_SCHEMA   (de MAD)
debounce/                junta mensajes en ráfaga con Redis                    (de MAD)
input_guardrails/        guardrail de entrada LOCAL, 6 capas sin red (before_agent) (de MAD)
guardrails_model_armor/  Model Armor de Google Cloud: reemplaza a las capas de Groq (APAGADO)
observabilty/            Langfuse + máscara de cédula/teléfono                 (de MAD)
migrations/              SQL idempotente (ver migrations/README.md)
```

Las cuatro carpetas marcadas "de MAD" se copiaron del proyecto de Madeline Cañizares,
que está en producción, y solo se cambiaron textos. Si se corrige un bug ahí, conviene
traerlo aquí, y al revés.

## Decisiones (y su porqué)

1. **Botones → menú numerado.** El guion pide botones, pero el WhatsApp del candidato
   está conectado con Baileys (no oficial), que no admite mensajes interactivos. Las
   opciones salen numeradas y el modelo interpreta "3" según el **último** mensaje que
   envió. Verificado con el LLM real.
2. **RAG armado pero apagado.** Igual que en MAD, a Qdrant se carga la **Ayuda Memoria
   Integral** del candidato (`rag/rag.py`, una sección numerada = un chunk). Pero el
   cliente entregó la **plantilla vacía**, así que:
   - `rag/rag.py` se niega a cargar si hay menos de 5 secciones con texto o si aparece
     texto de otro perfil ("Medicina"). Verificado: con la plantilla actual, rechaza.
   - `buscar_conocimiento` existe pero **no** está en `TOOLS_CABLEADAS`. Hoy todo sale
     del guion del prompt.
   - **Para activarlo:** poner la Ayuda Memoria completa en `rag/data/`, correr
     `python rag/rag.py --dry-run` (revisar que los títulos de sección salgan bien: el
     PDF de la plantilla los extrae pegados), luego `python rag/rag.py`, cablear la
     tool (pasos en su docstring) y recalibrar `RETRIEVAL_MIN_SCORE`.
   - Embeddings: **`text-embedding-3-large`** (3072 dim), por la calidad en español
     (decisión del 2026-10-21). La ingesta y la tool leen el mismo `EMBEDDING_MODEL`.
   - Qdrant: la instancia de la agencia en EasyPanel, la misma de MAD, por HTTPS +
     `api-key` (no hace falta SSH). Colección propia `tenant_id_campana_toto_reyes`
     (todavía no existe).
3. **El teléfono no es argumento de la tool.** Lo pone el canal en el
   `ConversacionContext` desde el `remoteJid`. Si fuera argumento, lo escribiría el
   modelo. Hay un test que lo vigila.
4. **`ok=true` antes de confirmar.** El prompt solo dice que algo quedó registrado si
   la tool devolvió `ok: true`. Es la regla que en MAD salió de un incidente real.
5. **Postgres manda; Apps Script es opcional.** El guion pide un POST a Google Apps
   Script. Se hace si `APPS_SCRIPT_WEBHOOK_URL` está definida, pero la copia que manda es
   `campana_toto_reyes.simpatizantes`. Si el Apps Script falla y Postgres guardó, `ok`
   sigue siendo true.
6. **Un esquema por tenant** (`DB_SCHEMA=campana_toto_reyes`): la tabla de leads y las 4
   tablas del checkpointer viven ahí. El namespace de Redis y la etiqueta de Langfuse
   salen de la misma variable.
7. **Sin PIIMiddleware.** Su detector de direcciones redacta el barrio, que es justo el
   dato que se capta (pasó en AVANZA). El teléfono no pasa por el modelo.
8. **El webhook responde 200 al instante.** El trabajo sigue en una tarea: el debounce
   (15 s) más el LLM pasa de los 10 s que Conecta espera, y si no respondemos a
   tiempo Conecta reintenta (hasta 8 veces) y la persona recibe varias respuestas. Además se deduplica
   por `X-Conecta-Event-Id` en Redis.

## SmartGob Conecta: lo que falta para salir a producción

- ✅ Credencial (`SMARTGOB_API_KEY`, sgc_…) y conexión "TOTO REYES"
  (`SMARTGOB_CONNECTION_ID`, modo BAILEYS, estado OPEN) verificadas el 2026-10-21.
- 🛑 **Registrar el webhook** en el panel de Conecta: Administración → Integraciones →
  Webhooks (requiere administrador de la organización con segundo factor).
  URL `https://<dominio>/webhook/conecta` (solo https público), evento
  `message.received`. El panel muestra el **secreto de firma UNA sola vez**: va en
  `SMARTGOB_WEBHOOK_SECRET`. Sin él, el canal no verifica la firma (avisa al arrancar).
- Con el botón "Evento de prueba" del panel llega un `webhook.test` firmado: sirve para
  comprobar URL y secreto sin que el bot responda nada.
- ⚠️ **El JSON de `message.received` no está documentado.** El parser
  (`smartgob_conecta/webhook.py`) es tolerante y está probado con varias formas, pero
  hay que confirmarlo con el primer mensaje real: el canal registra en el log la
  ESTRUCTURA (solo claves, sin valores) de los 5 primeros webhooks.
- Base de datos y Redis **propios de esta campaña**. El `.env` local apunta a los
  contenedores de `docker-compose.dev.yml`. No reutilizar los de MAD.

## Registro y colaboraciones (flujo v2)

- **En `main` desde el 2026-10-24.** Antes de Implementar hacen falta
  `PERSONA_FUENTE=ventanilla` y las credenciales de la ventanilla: sin la DINARDAP
  nadie se registra (los nombres salen de ahí). `GET /health` → `registro_civil`.
- **Sin aviso de privacidad (F02 descartado).** El documento lo pedía obligatorio,
  pero Giovani dijo que no hay enlace de privacidad y se quitó (2026-10-24). ⚠️ El bot
  pide cédula, correo y teléfono sin aviso ni consentimiento (LOPDP): decisión del
  cliente.
- **El orden lo manda el backend**, no el prompt: `dominio/registro.py` calcula el
  `paso_pendiente` (CEDULA → CONFIRMAR_NOMBRES → CORREO → TELEFONO → BARRIO
  → REVISAR_PERFIL → COMPLETO). Las tools lo devuelven como `siguiente_paso` y el
  middleware lo pone en `<estado_del_registro>`.
- **Los nombres solo salen de la DINARDAP** (`integraciones/persona/`, el adapter de
  MAD). Si la consulta falla, no se registra: Reintentar o Salir. No hay vía de nombre
  manual (el documento dice que no está aprobada). Los registros del guion v1 tienen
  cédula sin nombres verificados: `consultar_cedula("")` usa la guardada.
- **Todos los campos son obligatorios** (criterio del documento, pendiente de
  validar). El teléfono de contacto es aparte del WhatsApp; "el mismo de este número"
  → `usar_whatsapp=true`, lo copia el backend.
- **Corregir un dato del perfil deshace su confirmación** y obliga a revisar el
  resumen otra vez. "Cancelar registro" borra la fila.
- **Colaboraciones idempotentes:** confirmar es un `update ... where estado =
  'borrador'`; repetirlo no duplica. Sin conexión a la base → `FALLO`; error a mitad
  → `RESULTADO_INCIERTO`, y "Comprobar estado" vuelve a llamar a la misma tool. Al
  confirmar se guarda la copia de correo y teléfono que la persona revisó.
- El modelo nunca ve la cédula; correo y teléfono le llegan enmascarados.
- El guardrail de entrada bloqueaba todo mensaje con correo (leía `gmail.com` como un
  enlace). Corregido el 2026-10-22: los correos se quitan antes del filtro de URLs.

**Pendiente del cliente (página 10 del documento):** confirmar que todos los campos
son obligatorios; a qué teléfono
se contacta; cómo consulta el equipo (acceso a Postgres o vista en SmartConecta); vía
real para atención humana, corrección y borrado de datos. Mientras tanto el bot dice
con claridad que eso no se puede hacer desde el chat.

## Puntos a vigilar

- **Guardrail: Groq → Model Armor (2026-10-23).** Las antiguas capas 7 y 8 (modelos
  de IA en Groq) se ELIMINARON por decisión del cliente: eran modelos "Preview" (no
  aptos para producción) y su límite gratuito (2.000 tokens/min, compartido con MAD)
  dejó sin respuesta a la gente. Las reemplaza **Model Armor**
  (`guardrails_model_armor/`), ya programado y probado sin GCP pero **APAGADO**: el
  middleware está comentado en `agent.py` y el SDK en `requirements.txt` hasta que
  Giovani pase la cuenta de GCP. Pasos en `guardrails_model_armor/README.md`.
  Mientras tanto protegen las 6 capas locales de `input_guardrails/`.
- **Langfuse**: proyecto compartido con otras campañas. Las trazas van etiquetadas con
  `campana_toto_reyes`.
