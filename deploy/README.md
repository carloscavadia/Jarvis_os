# Despliegue de JARVIS_OS

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
   $EDITOR .env          # elige proveedor y pon tu API key
   ```

## Opción 1 — Docker Compose (recomendado)

Levanta el broker MQTT + el gateway en un comando:

```bash
cd deploy
docker compose up -d
docker compose logs -f gateway
```

- Gateway (REST/WebSocket): `http://<ip-del-servidor>:8080`
- Broker MQTT: `<ip-del-servidor>:1883`
- Reinicio automático activado (`restart: always`).

Prueba desde otro equipo de la red:

```bash
curl -X POST http://<ip-del-servidor>:8080/chat \
     -H 'Content-Type: application/json' \
     -d '{"message": "¿qué hora es?"}'
```

## Opción 2 — systemd (sin Docker)

```bash
sudo useradd --system --home /opt/jarvis_os jarvis      # usuario de servicio
sudo mkdir -p /opt/jarvis_os && sudo chown jarvis: /opt/jarvis_os
# copia el proyecto a /opt/jarvis_os, crea el venv e instala:
python3 -m venv /opt/jarvis_os/.venv
/opt/jarvis_os/.venv/bin/pip install "./core[openai]" ./gateway

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
- El broker MQTT de ejemplo es anónimo (solo LAN de confianza). Para dispositivos que salen
  a Internet por 4G, usa **credenciales + TLS** (ver `mosquitto.conf`).
- La **memoria interna** de JARVIS se guarda en `data/jarvis_memory.db` (volumen persistente
  en Docker, o carpeta del proyecto en systemd). Haz copia de seguridad de ese fichero.

## En Proxmox

Crea un **LXC** (ligero) o una **VM** con Debian/Ubuntu y sigue cualquiera de las dos
opciones dentro. 2 vCPU / 2 GB bastan si el cerebro es una API remota (Claude o NVIDIA).
Sube recursos y añade GPU (passthrough) solo si vas a correr modelos o voz **en local**.
