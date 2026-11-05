# Agente WhatsApp · Juan José "Toto" Reyes Baquerizo

Asistente de WhatsApp de la campaña **"Toto Reyes · Concejal"** (Guayaquil). Hecho
con LangChain v1 (`create_agent`) + LangGraph sobre FastAPI. El canal es la API de
**SmartGob Conecta** (plataforma de Origami que corre Evolution API por debajo), por
indicación del cliente (2026-10-21).

Es **TotoBot** y hace tres cosas (flujo v4, `TOTOBOT (1).docx`, 2026-11-05):
1. **Registro corto obligatorio antes del menú**: cédula consultada en la **DINARDAP**
   (los nombres salen de ahí, sin pedir confirmación) → correo → celular (09…) →
   barrio → "Ya quedaste registrado". Tabla `ciudadanos`. La bienvenida trae el
   consentimiento ("al ingresar los datos, aceptas…"). El middleware `EstadoRegistro`
   le dice al modelo, en cada turno, en qué paso está la persona (`paso_pendiente`).
2. **Presenta al candidato**: Biografía y Propuestas, con los textos del documento.
3. **Recibe ideas** con "Quiero unirme a la campaña" / "Quiero hacer una propuesta"
   (mismo camino): idea → "¿Está correcta?" (1 enviar / 2 modificar) → cierre. Tabla
   `simpatizantes` (estado borrador / confirmada / cancelada). **Registro e ideas son
   los datos que le importan al cliente.**

## Fuente de verdad del contenido

En `../` (carpeta del cliente):

| Documento | Uso |
|---|---|
| `TOTOBOT (1).docx` ("FLUJO PROPUESTA V4", recibido 2026-11-05) | **Fuente de verdad (flujo v4).** Textos casi literales en `<guion>` del prompt. Los huecos del documento y cómo se resolvieron están en la cabecera de `prompt/prompt.yaml` y abajo. |
| `Flujo_Agente_Juan_Jose.docx` (2026-10-24) | Flujo v2, reemplazado por el v4. |
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
python -m pytest -q                                     # 135 tests (OJO: los de integración usan la base del .env, hoy el Supabase real: correr con `DATABASE_URL= DB_HOST=` o con una base local)
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
model_config/            parámetros del LLM (gpt-6.1-sol, reasoning low, Responses API; desde 2026-10-24)
prompt/prompt.yaml       system prompt en formato TAGS, con el guion del cliente
tools/                   registro_tool (2 tools) + colaboracion_tool (2 tools); buscar_conocimiento (SIN cablear)
middleware/              EstadoRegistro: agrega <estado_del_registro> al system prompt en cada llamada
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

## Registro e ideas (flujo v4)

- **Rama `flujo-v4`** (2026-11-05). Reemplaza al v2: sin confirmar nombres, sin
  resumen del perfil, sin "Cancelar registro" y sin paso de autorización en "Quiero
  unirme". Tools: `consultar_cedula`, `guardar_datos_contacto`,
  `preparar_colaboracion`, `confirmar_colaboracion`.
- **El orden lo manda el backend**, no el prompt: `dominio/registro.py` calcula el
  `paso_pendiente` (CEDULA → CORREO → TELEFONO → BARRIO → COMPLETO). Las tools lo
  devuelven como `siguiente_paso` y el middleware lo pone en `<estado_del_registro>`.
  Quien quedó a medias en los pasos del v2 pasa solo al dato que le falte.
- `nombres_confirmados` y `registro_confirmado_en` se siguen llenando (al consultar la
  cédula y al completar el barrio) para que el equipo sepa desde cuándo está
  registrado. `migrations/005_flujo_v4.sql` (opcional) marca a los del v2.
- **Validaciones en las tools:** celular de 10 dígitos que empieza con 09 (también
  +593 9…; fijos y extranjeros no); barrio con al menos 2 letras; idea de al menos
  10 caracteres (`IDEA_CORTA`).
- **Los nombres solo salen de la DINARDAP** (`integraciones/persona/`). Si la
  consulta falla, no se registra: Reintentar o Salir. Hace falta
  `PERSONA_FUENTE=ventanilla` + credenciales. `GET /health` → `registro_civil`.
- **Ideas idempotentes:** confirmar es un `update ... where estado = 'borrador'`;
  repetirlo no duplica. Sin conexión → `FALLO`; error a mitad → `RESULTADO_INCIERTO`.
- El modelo nunca ve la cédula; correo y teléfono le llegan enmascarados.
- El guardrail de entrada bloqueaba todo mensaje con correo (leía `gmail.com` como un
  enlace). Corregido el 2026-10-22: los correos se quitan antes del filtro de URLs.

**Huecos del documento v4 (decididos por nosotros, confirmar con el cliente):** el
menú tiene dos "3" → quedaron 3 y 4, con el mismo camino; "Proyectos" sin texto → no
se ofrece; menú después del cierre distinto al principal → se usa el principal; falta
el enlace del canal de difusión → la línea se omite; "MENÚ en cualquier momento" →
durante el registro no abre el menú.

**No implementado del v4:** recordatorio de inactividad (10 min; requiere envío
programado) y respuesta a fotos/stickers sin texto (el parser de
`smartgob_conecta/`, compartido con MAD, los descarta). Los audios se siguen
transcribiendo; si falla, se responde el texto de "solo puedo leer mensajes escritos".

## El modelo: `gpt-6.1-sol` por la Responses API (desde 2026-10-24)

Recorrido del día: gpt-4.1 → gpt-5.1 → gpt-5.6-terra → gpt-5.6-sol → gpt-6-sol → gpt-6.1-sol. No basta con cambiar el nombre en `model_config.yaml`:

- **Razona siempre.** No acepta `reasoning_effort: none` ni `temperature`; va con
  `low`. `agent.py` solo pasa a `init_chat_model` las opciones que el yaml declara.
- **Chat Completions no sirve:** ahí las tools solo funcionan con razonamiento
  `none`. Por eso `use_responses_api: true`.
- **El `content` del `AIMessage` es una LISTA de bloques** (razonamiento + texto).
  La respuesta se lee con `.text`; leer `.content` mandaría basura al ciudadano.
- **El razonamiento cuenta dentro de `max_tokens`.** Con un tope bajo la respuesta
  se corta y `.text` llega vacío: por eso `max_tokens: 4096` y, si no hay texto, se
  manda `MENSAJE_ERROR` en vez de nada.
- Hilos empezados con el modelo anterior siguen funcionando. **Sin probar:** hilos
  de semanas, porque el checkpoint guarda bloques de razonamiento (`rs_…`) que se
  reenvían en cada turno. Si un chat largo empieza a fallar, mirar ahí primero.
- Latencia: 2-3 s en turnos simples, 5-7 s con tools (gpt-4.1 era más rápido).

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
