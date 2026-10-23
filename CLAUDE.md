# Agente WhatsApp · Juan José "Toto" Reyes Baquerizo

Asistente de WhatsApp de la campaña **"Toto Reyes · Concejal"** (Guayaquil). Hecho
con LangChain v1 (`create_agent`) + LangGraph sobre FastAPI. El canal es la API de
**SmartGob Conecta** (plataforma de Origami que corre Evolution API por debajo), por
indicación del cliente (2026-10-21).

Hace tres cosas (guion v1, 2026-10-22):
1. **Registra a la persona en la bienvenida**: cédula, nombres completos, barrio y
   correo (opcional), con la tool `registrar_ciudadano` → tabla `ciudadanos`. **Solo
   pide lo que falta**: el middleware `EstadoRegistro` le pasa al modelo, en cada
   turno, qué datos ya están en la base para ese número de WhatsApp.
2. **Presenta al candidato** siguiendo el guion que aprobó el cliente: quién es, su
   familia, sus valores, su formación y sus proyectos.
3. **Capta simpatizantes** con "Quiero sumarme": ya NO pide nombre ni barrio (están en
   el registro), solo cómo quiere ayudar → `guardar_simpatizante` → tabla
   `simpatizantes`. **Registro y captura son los datos que le importan al cliente.**

## Fuente de verdad del contenido

En `../` (carpeta del cliente):

| Documento | Uso |
|---|---|
| `FlujoTTRBOT v1.docx` (2026-10-22) | **Fuente de verdad.** Guion v1, casi literal en `<guion>` del prompt. Lo que está en ROJO (pasos 1 y 2 de "Quiero sumarme") se omite: el cliente aclaró que esos datos ya están en la base y solo hay que pedir los que falten. |
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
python -m pytest -q                                     # 66 tests (el de integración necesita el Postgres local)
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
model_config/            parámetros del LLM (gpt-4.1, temperatura 0.2)
prompt/prompt.yaml       system prompt en formato TAGS, con el guion del cliente
tools/                   registrar_ciudadano + guardar_simpatizante (cableadas); buscar_conocimiento (SIN cablear)
middleware/              EstadoRegistro: agrega <estado_del_registro> al system prompt en cada llamada
dominio/                 validación pura de cédula (módulo 10) y correo               (de MAD)
rag/rag.py               ingesta de la Ayuda Memoria a Qdrant (rag/data/, no versionado) (de MAD)
simpatizantes/           repositorio de ciudadanos y simpatizantes: Postgres + Apps Script (opcional)
smartgob_conecta/        config, cliente (POST /messages, audios) y firma/parser del webhook
transcripcion.py         notas de voz -> texto (whisper-1)
conversation_history/    checkpointer PostgresSaver en el esquema DB_SCHEMA   (de MAD)
debounce/                junta mensajes en ráfaga con Redis                    (de MAD)
input_guardrails/        guardrail de entrada de 8 capas (before_agent)        (de MAD)
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
4. **`ok=true` antes de confirmar.** El prompt solo envía "Recibí tu información…" si
   `guardar_simpatizante` devolvió `ok: true`. Es la regla que en MAD salió de un
   incidente real.
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

## Registro de la bienvenida (guion v1)

- Obligatorios para que el registro quede completo: cédula, nombres y barrio. El
  correo se pide pero es opcional (decisión nuestra: el guion no dice qué hacer si no
  lo tienen, y perder el registro por un correo es peor).
- **Cada dato se guarda apenas llega**, aunque falten otros. La primera versión
  esperaba a tener todo y, probando con el modelo real, se perdían datos.
- La cédula se valida en el backend (`dominio/identidad.py`, el algoritmo de MAD). El
  modelo nunca la ve en `<estado_del_registro>` ni la repite.
- El registro no bloquea la información: si la persona pregunta antes de registrarse,
  se le responde y se le piden los datos (como mucho dos veces).
- Una fila por número de WhatsApp (índice único parcial sobre `telefono`).
- ⚠️ **Datos personales (LOPDP):** ahora se guarda cédula y correo. El guion v1 no
  trae ningún aviso de tratamiento de datos ni consentimiento. Pendiente de que el
  cliente decida si agrega una línea de consentimiento a la bienvenida.
- El guardrail de entrada bloqueaba todo mensaje con correo (leía `gmail.com` como un
  enlace). Corregido el 2026-10-22: los correos se quitan antes del filtro de URLs.

## Puntos a vigilar

- **Latencia del guardrail.** Las capas 7 y 8 llaman a Groq. En una prueba, Groq
  respondió con rate limit y su cliente esperó 20 s antes de reintentar. Si se repite en
  producción, bajar los reintentos del cliente de Groq en
  `input_guardrails/llama_guard_service.py`.
- **Langfuse**: proyecto compartido con otras campañas. Las trazas van etiquetadas con
  `campana_toto_reyes`.
