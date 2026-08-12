# Imagen del gateway de JARVIS_OS (incluye el núcleo).
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema mínimas.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Instalar el núcleo y el gateway.
COPY core/ ./core/
COPY gateway/ ./gateway/
COPY README.md ./README.md

# El núcleo con soporte OpenAI/NVIDIA y voz local Whisper + Piper.
RUN pip install --no-cache-dir "./core[openai,voice]" ./gateway

# Voz española predeterminada (MIT, es_MX). Whisper descarga su modelo en el volumen
# persistente durante la primera transcripción.
RUN mkdir -p /app/voices \
    && cd /app/voices \
    && python -m piper.download_voices es_MX-ald-medium

# Carpeta de datos (memoria interna persistente; se monta como volumen).
RUN addgroup --system jarvis \
    && adduser --system --ingroup jarvis --home /app jarvis \
    && mkdir -p /app/data \
    && chown -R jarvis:jarvis /app/data
ENV JARVIS_MEMORY_DB=/app/data/jarvis_memory.db
ENV JARVIS_TASKS_DB=/app/data/jarvis_tasks.db
ENV JARVIS_STT_DOWNLOAD_ROOT=/app/data/models
ENV JARVIS_TTS_MODEL_PATH=/app/voices/es_MX-ald-medium.onnx

USER jarvis

EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "jarvis_gateway.app"]
