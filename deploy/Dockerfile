# Imagen del gateway de JARVIS_OS (incluye el núcleo).
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema mínimas.
# espeak-ng es el fonemizador que Kokoro usa para español: sin él, el TTS falla.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    espeak-ng \
    nodejs \
    npm \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Instalar el núcleo y el gateway.
COPY core/ ./core/
COPY gateway/ ./gateway/
COPY README.md ./README.md
# El HUD viaja dentro de la imagen para que el gateway lo sirva en /hud y no
# pueda quedarse desparejado con el servidor.
COPY clients/ ./clients/

# El núcleo con soporte OpenAI/NVIDIA y voz local Whisper + Kokoro.
RUN pip install --no-cache-dir "./core[openai,voice]" ./gateway

# Navegador que JARVIS conduce. Opcional y apagado por defecto: Chromium y sus
# librerías añaden ~700 MB a la imagen, así que solo entra si se pide
# explícitamente. Para construir con él:
#   docker compose build --build-arg INSTALL_BROWSER=true
# y después JARVIS_BROWSER_ENABLED=true en el .env.
ARG INSTALL_BROWSER=false
# Fuera de /root a propósito: el contenedor corre como el usuario `jarvis`, que
# no puede leer el home de root. Con la ruta por defecto la imagen se construye
# sin quejarse y luego Chromium no arranca en ejecución.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
      pip install --no-cache-dir "./core[browser]" \
      && playwright install --with-deps chromium \
      && chmod -R a+rX /opt/ms-playwright; \
    fi

# Modelo de la palabra de activación (~6 MB). Se baja aquí, como root, porque
# openWakeWord lo guarda dentro de site-packages: en ejecución el usuario jarvis
# no podría escribir ahí. Además evita depender de la red en el primer arranque.
RUN python -c "from openwakeword.utils import download_models; download_models(model_names=['hey_jarvis'])"

# Carpeta de datos (memoria interna persistente; se monta como volumen).
RUN addgroup --system jarvis \
    && adduser --system --ingroup jarvis --home /app jarvis \
    && mkdir -p /app/data/cache /app/data/huggingface \
    && mkdir -p /app/workspace /app/.local \
    && chown -R jarvis:jarvis /app/data /app/workspace /app/.local
ENV JARVIS_MEMORY_DB=/app/data/jarvis_memory.db
ENV JARVIS_TASKS_DB=/app/data/jarvis_tasks.db
ENV JARVIS_GOALS_DB=/app/data/jarvis_goals.db
ENV JARVIS_STT_DOWNLOAD_ROOT=/app/data/models
# Kokoro descarga sus pesos (~330 MB) en HF_HOME durante la primera síntesis.
ENV JARVIS_TTS_VOICE=em_alex
ENV JARVIS_HUD_PATH=/app/clients/web-hud/index.html
ENV JARVIS_WORKSPACE_ROOT=/app
ENV XDG_CACHE_HOME=/app/data/cache
ENV HF_HOME=/app/data/huggingface
ENV PATH=/app/.local/bin:${PATH}

USER jarvis

EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "jarvis_gateway.app"]
