# =============================================================================
# Agente WhatsApp · Toto Reyes — canal SmartGob Conecta (FastAPI). Imagen para EasyPanel.
# =============================================================================
# Sirve `main_conecta:app` con uvicorn. Postgres, Redis y SmartGob Conecta son
# SERVICIOS EXTERNOS: no se levantan aquí. Toda la configuración se inyecta por
# variables de entorno desde el panel (el `.env` NO entra en la imagen).
#
# Build:  docker build -t agente-toto-reyes .
# Run:    docker run -p 8000:8000 --env-file .env agente-toto-reyes

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
url=f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/health'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).getcode()==200 else 1)"

# `exec`: uvicorn queda como PID 1 y recibe SIGTERM (cierra pools en el lifespan).
CMD ["sh", "-c", "exec uvicorn main_conecta:app --host 0.0.0.0 --port ${PORT:-8000}"]
