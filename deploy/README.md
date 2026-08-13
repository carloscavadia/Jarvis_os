# Despliegue de JARVIS_OS

La guía paso a paso recomendada está en
[`docs/ubuntu-server.md`](../docs/ubuntu-server.md).

Dos formas de dejar JARVIS corriendo **24/7 en un Ubuntu Server** accesible desde el resto
de equipos de tu red (y desde Proxmox, dentro de un LXC/VM).

> **Encendido siempre, sin gastar.** El servicio queda en reposo hasta que alguien le habla;
> no consume tokens ni CPU relevante mientras nadie interactúa. Si además usas un modelo
> **gratuito** (p.ej. **GLM-5.2 de Z.ai vía NVIDIA NIM**), puedes tenerlo encendido de forma
> permanente sin coste por uso. Configúralo en `.env` (opción B).

## Requisitos previos

1. Ubuntu Server (o un LXC/VM Debian en Proxmox).
2. `python3.11+` o Docker.
3. Un `.env` en la raíz del proyecto:
   ```bash
   cp deploy/.env.example .env
   $EDITOR .env          # configura el LLM y JARVIS_GATEWAY_API_KEY
   ```

## Opción 1 — Docker Compose (recomendado)

Levanta el broker MQTT + el gateway en un comando:

```bash
cd deploy
docker compose --env-file ../.env up -d
docker compose logs -f gateway
```

- Gateway (REST/WebSocket): `http://<ip-del-servidor>:8080`
- Broker MQTT: ligado a `127.0.0.1:1883` y desactivado en el gateway por defecto.
- Reinicio automático activado (`restart: always`).

La imagen incluye Kokoro con la voz española `em_alex`. El primer uso descarga los modelos
de Kokoro y Whisper configurado (`small` por defecto) al volumen persistente, por lo que
esa primera interacción tarda más.
Puedes desactivar toda la voz local con `JARVIS_VOICE_ENABLED=false` en `.env`.

Comprueba el servicio de voz con:

```bash
curl -H 'X-Jarvis-Key: <JARVIS_GATEWAY_API_KEY>' \
  http://<ip-del-servidor>:8080/voice/status
```

### Workspace y control agéntico

JARVIS puede crear carpetas y archivos, leerlos y solicitar permiso para modificar los ya
existentes. En Docker todo queda dentro de los volúmenes persistentes `jarvis_workspace` y
`jarvis_packages`; no tiene acceso al sistema de archivos completo ni puede modificar el
host o Docker.

Prueba desde el HUD con: `crea la carpeta informes y dentro un archivo estado.txt`.
Para ver el contenido persistente desde Ubuntu:

```bash
sudo docker exec jarvis-gateway find /app/workspace -maxdepth 3 -type f -print
```

La instalación `pip` ocurre en `/app/.local` y siempre requiere aprobación. Los paquetes
APT del host permanecen desactivados en Docker. Si se usa el despliegue systemd, se puede
habilitar `apt` con `JARVIS_PACKAGE_INSTALL_MANAGERS=apt,pip`, pero solo después de crear
manualmente una política `sudoers` limitada para el usuario `jarvis`.

Los modelos y cachés de Whisper/Hugging Face se guardan en `/app/data`, que es escribible
por el usuario no privilegiado del gateway y persiste en el volumen `jarvis_data`.

JARVIS también puede ejecutar archivos `.py` ya creados dentro del workspace. Cada
ejecución solicita aprobación en el HUD, no acepta código inline ni shell y termina al
agotarse `JARVIS_PYTHON_EXECUTION_TIMEOUT_SECONDS`.

El acceso web de solo lectura está activo por defecto en Docker. Bloquea destinos privados,
locales y reservados, admite únicamente HTTPS y limita tiempo, redirecciones y tamaño. La
búsqueda funciona con Bing RSS; para resultados más fiables configura opcionalmente
`JARVIS_BRAVE_SEARCH_API_KEY` en `.env`.

En proveedores NVIDIA, `JARVIS_OPENAI_ENABLE_THINKING=false` evita que el razonamiento
interno aparezca en el HUD. Las rondas que llaman herramientas descartan cualquier texto
preliminar y solo la respuesta final se entrega al usuario y al sintetizador de voz.
El idioma de una fuente web no cambia `JARVIS_LANGUAGE`: los resultados se traducen y se
redactan en el idioma configurado, manteniendo nombres propios y URLs originales.

`JARVIS_HUD_WORKSPACE_ENABLED=true` permite que el agente use `show_in_workspace` para
presentar contenido separado del chat. El HUD mantiene esas presentaciones abiertas hasta
que el usuario las cierre.

Los conectores de Gmail, Outlook, WhatsApp y otros sistemas se integran mediante n8n. Las
credenciales permanecen en n8n, las consultas se limitan a una lista declarada y toda acción
de escritura pide aprobación en el HUD. Consulta [`docs/connectors.md`](../docs/connectors.md)
para crear el webhook y habilitar las variables `JARVIS_N8N_*`.

Prueba desde otro equipo de la red:

```bash
curl -X POST http://<ip-del-servidor>:8080/chat \
     -H 'Content-Type: application/json' \
     -H 'X-Jarvis-Key: <JARVIS_GATEWAY_API_KEY>' \
     -d '{"message": "¿qué hora es?"}'
```

## Opción 2 — systemd (sin Docker)

```bash
sudo useradd --system --home /opt/jarvis_os jarvis      # usuario de servicio
sudo mkdir -p /opt/jarvis_os && sudo chown jarvis: /opt/jarvis_os
# copia el proyecto a /opt/jarvis_os, crea el venv e instala:
python3 -m venv /opt/jarvis_os/.venv
/opt/jarvis_os/.venv/bin/pip install "./core[openai,voice]" ./gateway

# instala el broker MQTT
sudo apt install mosquitto

# instala y arranca el servicio (arranca en cada boot):
sudo cp deploy/systemd/jarvis-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-gateway
sudo systemctl status jarvis-gateway
```

## Notas de red y seguridad

- El gateway escucha en `0.0.0.0` → accesible en toda la LAN. Si expones a Internet, ponlo
  detrás de un reverse proxy con **TLS** o de una **VPN (WireGuard)**.
- MQTT no se publica en la LAN por defecto. Antes de conectar HUB físicos, configura
  usuarios, ACL y TLS en Mosquitto y cambia conscientemente el enlace de puertos.
- El gateway no registra la herramienta de shell; esa capacidad queda limitada a la CLI local.
- La **memoria interna** de JARVIS se guarda en `data/jarvis_memory.db` (volumen persistente
  en Docker, o carpeta del proyecto en systemd). Haz copia de seguridad de ese fichero.

## En Proxmox

Crea un **LXC** (ligero) o una **VM** con Debian/Ubuntu y sigue cualquiera de las dos
opciones dentro. 2 vCPU / 2 GB bastan si el cerebro es una API remota (Claude o NVIDIA).
Sube recursos y añade GPU (passthrough) solo si vas a correr modelos o voz **en local**.
