# Imagen del gateway de JARVIS_OS (incluye el núcleo).
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema mínimas.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Instalar el núcleo y el gateway.
COPY core/ ./core/
COPY gateway/ ./gateway/
COPY README.md ./README.md

# El núcleo con soporte openai (para NVIDIA/Ollama) y el gateway.
RUN pip install --no-cache-dir "./core[openai]" ./gateway

# Carpeta de datos (memoria interna persistente; se monta como volumen).
RUN mkdir -p /app/data
ENV JARVIS_MEMORY_DB=/app/data/jarvis_memory.db

EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "jarvis_gateway.app"]
