# Agente WhatsApp · Toto Reyes

Asistente de WhatsApp de la campaña de Juan José "Toto" Reyes Baquerizo (Concejal,
Guayaquil). Presenta al candidato con el guion aprobado y capta simpatizantes
(nombre, barrio y cómo quiere ayudar).

LangChain v1 + LangGraph · FastAPI · SmartGob Conecta (WhatsApp) · Postgres · Redis · Langfuse.

## Arranque local

```bash
conda create -n toto-reyes python=3.11 -y && conda activate toto-reyes
pip install -r requirements-dev.txt
cp .env.example .env            # completar
docker compose -p toto-reyes-dev -f docker-compose.dev.yml up -d
python -m pytest -q
python main.py                  # conversar por consola
uvicorn main_conecta:app --reload --port 8000
```

## Despliegue

`Dockerfile` listo para EasyPanel (puerto `8000`, healthcheck en `/health`). Las
variables van en el panel, no en la imagen.

En SmartGob Conecta (Administración → Integraciones → Webhooks), crear el webhook:

- URL: `https://<dominio>/webhook/conecta`
- Evento: `message.received`
- Copiar el secreto de firma que muestra el panel (una sola vez) en `SMARTGOB_WEBHOOK_SECRET`.

## Datos captados

`campana_toto_reyes.simpatizantes`: `telefono`, `nombre`, `barrio`, `ayuda`,
`nombre_perfil`, `canal`, `creado_en`. Ver `migrations/`.

Más detalle de las decisiones en `CLAUDE.md`.
