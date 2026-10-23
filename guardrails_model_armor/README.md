# Model Armor (Google Cloud): guardrail de entrada

Reemplaza a las antiguas capas 7 y 8 del guardrail (modelos de IA en Groq), por
decisión del cliente (2026-10-23). Las capas locales 1-6 de `input_guardrails/` siguen
activas y corren antes.

**Estado: programado pero APAGADO.** El agente funciona sin él. Falta la cuenta de GCP.

## Qué revisa

| Filtro de la plantilla | Si coincide, el bot responde | Notas |
|---|---|---|
| Inyección de prompt / jailbreak | `jailbreak_ia` | Probado por Google en español |
| IA responsable: odio, acoso, sexual, peligroso | `contenido_ia_bloqueado` | Umbral `HIGH`, para no frenar mensajes políticos normales |
| URLs maliciosas | `dominio_bloqueado` | Las capas locales ya bloquean cualquier enlace que no sea `.gob.ec` |
| CSAM | `contenido_ia_bloqueado` | Lo agrega Google en las regiones con soporte completo |
| Datos sensibles (SDP) | **No bloquea** | NO activarlo: el bot pide cédula y correo en el registro |

Las respuestas triviales del menú ("1", "sí", "gracias") no se envían a Model Armor.
Si Model Armor no responde, el mensaje sigue (`MODEL_ARMOR_FAIL_CLOSED=false`).

## Cómo activarlo

1. **API y plantilla** (en el proyecto de GCP que pase Giovani):

   ```bash
   gcloud config set project PROJECT_ID
   gcloud services enable modelarmor.googleapis.com
   # Si gcloud lo pide, apuntarlo al endpoint regional:
   gcloud config set api_endpoint_overrides/modelarmor "https://modelarmor.us-central1.rep.googleapis.com/"

   gcloud model-armor templates create toto-reyes-entrada \
     --location=us-central1 \
     --rai-settings-filters='[{"filterType":"HATE_SPEECH","confidenceLevel":"HIGH"},{"filterType":"HARASSMENT","confidenceLevel":"HIGH"},{"filterType":"SEXUALLY_EXPLICIT","confidenceLevel":"HIGH"},{"filterType":"DANGEROUS","confidenceLevel":"HIGH"}]' \
     --pi-and-jailbreak-filter-settings-enforcement=enabled \
     --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above \
     --malicious-uri-filter-settings-enforcement=enabled
   ```

2. **Cuenta de servicio** con el rol `roles/modelarmor.user` en ese proyecto, y una
   clave JSON.

3. **Variables en EasyPanel** (ver `.env.example`):

   ```
   MODEL_ARMOR_PROJECT_ID=PROJECT_ID
   MODEL_ARMOR_LOCATION=us-central1
   MODEL_ARMOR_TEMPLATE_ID=toto-reyes-entrada
   MODEL_ARMOR_CREDENTIALS_JSON={...contenido del JSON de la cuenta de servicio...}
   ```

4. **Código:**
   - En `requirements.txt`, descomentar `google-cloud-modelarmor`.
   - En `agent.py`, descomentar el import de `construir_middleware_model_armor` y su
     línea en la lista `middleware=[...]`.
   - Correr `python -m pytest -q` y una prueba real con `python main.py`.

5. **Probar antes de desplegar** con mensajes reales en español: un saludo, el
   registro con cédula y correo (no debe bloquearse) y un intento de jailbreak (sí debe
   bloquearse). Si la plantilla frena mensajes legítimos, subir el umbral a `HIGH` en
   jailbreak.

## Costo

2 millones de tokens al mes sin costo; después, USD 0,10 por millón. Model Armor cuenta
1 token cada 4 caracteres: un mensaje de WhatsApp son ~12 tokens.
