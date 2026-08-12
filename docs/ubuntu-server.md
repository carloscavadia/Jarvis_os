# Instalar el servicio principal en Ubuntu Server

La ruta recomendada usa Docker Compose. Mantiene Python, Mosquitto y las dependencias
aisladas y permite actualizar o revertir el servicio de forma predecible.

## 1. Preparar Ubuntu

Requisitos recomendados: Ubuntu Server 24.04 LTS, 2 vCPU, 2 GB de RAM y 10 GB libres.

```bash
sudo apt update
sudo apt install -y ca-certificates curl git openssl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker --version
docker compose version
```

Si no quieres usar el instalador oficial de Docker, instala Docker Engine siguiendo su
documentación para Ubuntu.

## 2. Descargar JARVIS_OS

```bash
sudo mkdir -p /opt/jarvis_os
sudo chown "$USER":"$USER" /opt/jarvis_os
git clone https://github.com/carloscavadia/Jarvis_os.git /opt/jarvis_os
cd /opt/jarvis_os
```

Mientras el PR de seguridad no esté fusionado, usa temporalmente:

```bash
git checkout codex/security-baseline
```

## 3. Configurar secretos y proveedor

```bash
cp deploy/.env.example .env
chmod 600 .env
openssl rand -hex 32
nano .env
```

Copia el valor generado por OpenSSL en:

```dotenv
JARVIS_GATEWAY_API_KEY=pega-aqui-la-clave-generada
```

Configura exactamente un proveedor.

### Anthropic

```dotenv
JARVIS_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=tu-clave
JARVIS_MODEL=tu-modelo-compatible
```

### API compatible con OpenAI

```dotenv
JARVIS_LLM_PROVIDER=openai
JARVIS_OPENAI_BASE_URL=https://endpoint.example/v1
JARVIS_OPENAI_API_KEY=tu-clave
JARVIS_OPENAI_MODEL=nombre-del-modelo
```

### Ollama en otro equipo

```dotenv
JARVIS_LLM_PROVIDER=openai
JARVIS_OPENAI_BASE_URL=http://IP-DE-OLLAMA:11434/v1
JARVIS_OPENAI_API_KEY=ollama
JARVIS_OPENAI_MODEL=nombre-del-modelo
```

Mantén inicialmente:

```dotenv
JARVIS_MQTT_ENABLED=false
JARVIS_ENABLE_SHELL=true
```

Aunque la CLI local puede usar shell, el gateway remoto nunca registra esa herramienta.

## 4. Construir y arrancar

```bash
cd /opt/jarvis_os/deploy
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 gateway
```

El contenedor debe aparecer como `healthy`. La memoria y las tareas se conservan en el
volumen Docker `jarvis_data`.

## 5. Verificar el servicio

Desde el servidor:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready
```

Respuesta esperada:

```json
{"status":"ok","persona":"JARVIS","provider":"anthropic","scheduler":"on"}
{"status":"ready","provider":"anthropic"}
```

Prueba autenticada:

```bash
set -a
. /opt/jarvis_os/.env
set +a
curl -sS http://127.0.0.1:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-Key: $JARVIS_GATEWAY_API_KEY" \
  -d '{"message":"Responde únicamente: servidor operativo","session_id":"smoke-test"}'
```

- `/health` confirma que el proceso está vivo.
- `/ready` confirma que autenticación y proveedor tienen la configuración mínima.
- Si ambos funcionan pero `/chat` falla, revisa la validez de la API key, la URL y el
  nombre del modelo configurado para el proveedor.

## 6. Acceso desde la LAN

Obtén la IP:

```bash
hostname -I
```

Si UFW está activo, permite únicamente tu subred local; ajusta el ejemplo:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp
sudo ufw status
```

No publiques 8080 directamente en Internet. Para acceso exterior usa una VPN como
WireGuard/Tailscale o un reverse proxy HTTPS autenticado.

El HUB web se conecta a:

```text
ws://IP-DEL-SERVIDOR:8080/ws/hud
```

e introduce `JARVIS_GATEWAY_API_KEY` en el campo de clave del HUB.

## 7. Operación diaria

```bash
cd /opt/jarvis_os/deploy
docker compose ps
docker compose logs -f gateway
docker compose restart gateway
docker compose down
docker compose up -d
```

Actualización después de fusionar cambios:

```bash
cd /opt/jarvis_os
git pull --ff-only
cd deploy
docker compose up -d --build
```

## 8. Copias de seguridad

Consulta primero el nombre real del volumen:

```bash
docker volume ls | grep jarvis_data
```

La memoria y las tareas viven en ese volumen. Programa una copia periódica antes de
actualizaciones importantes. No copies ni publiques el fichero `.env`.

## MQTT y HUB físicos

MQTT permanece desactivado y ligado a localhost en la configuración inicial. No abras los
puertos 1883/9001 para ESP32 hasta disponer de usuarios, ACL por dispositivo y TLS. El
servidor REST/WebSocket puede funcionar completamente sin MQTT mientras desarrollamos el
HUB físico.
