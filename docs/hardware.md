# Guía de hardware

## Servidor (el cerebro)

**Proxmox VE** sobre cualquier máquina razonable. JARVIS corre dentro de un **LXC** o una
**VM**:

- **CPU/RAM:** el núcleo en sí es ligero (llama a la API de Claude). 2 vCPU / 2 GB bastan
  para empezar. Si añades **voz local** (Whisper/Kokoro) o **modelos LLM locales** (Ollama),
  sube a 4-8 vCPU / 8-16 GB y considera una **GPU** con passthrough para inferencia local.
- **Disco:** 20 GB de sobra para el software; más si guardas mucho audio/historial.
- **Red:** el contenedor necesita salida a Internet (API de Claude) y ser accesible por los
  dispositivos (directamente o vía VPN/reverse proxy con TLS).

## Dispositivos (las extremidades)

### LilyGo T-A7670G R2 — 4G LTE Cat-1 SIM + ESP32

La placa que vas a usar. Puntos clave:

| Característica | Detalle |
|---------------|---------|
| MCU           | ESP32 (WiFi + Bluetooth) |
| Módem         | SIMCom **A7670G** — 4G LTE **Cat-1** |
| Conectividad  | 4G (con SIM de datos) **o** WiFi |
| GPS           | Según variante ("G" suele traer GNSS) |
| Alimentación  | USB-C y conector de batería LiPo (JST) |
| Antenas       | LTE (obligatoria) y GPS/GNSS |

**Para qué sirve el 4G aquí:** que el dispositivo funcione **fuera de tu red local** —
en el coche, en el jardín, en otra casa— hablando con tu servidor por Internet móvil. Si el
dispositivo va a estar siempre en casa, WiFi es más simple y barato.

**Consideraciones de energía:** el módem 4G tiene picos de consumo altos al transmitir.
Usa una fuente/batería que aguante picos (>2 A) o verás reinicios. Añade condensadores si
alimentas por batería.

### Periféricos recomendados por dispositivo

- **Pantalla:** un panel I²C/SPI (SSD1306 OLED para algo simple, o TFT/ e-paper para más).
- **Micrófono:** I²S (p.ej. INMP441) para capturar voz con calidad.
- **Altavoz:** amplificador I²S (MAX98357A) + altavoz pequeño.
- **Botón / touch:** para activar la escucha sin depender solo de la palabra clave.

## Topología de red recomendada

```
Dispositivo ESP32 ──4G/WiFi──► Internet ──► [Reverse proxy TLS / VPN]
                                                     │
                                              Proxmox (tu casa)
                                                     │
                                         LXC/VM: gateway + broker MQTT + núcleo
```

- **Recomendado:** una **VPN** (WireGuard) o un **broker MQTT con TLS** expuesto tras un
  reverse proxy. **No** expongas MQTT sin cifrar a Internet.
- Da a cada dispositivo un **ID único** y **credenciales propias** para el broker.

## Checklist para un dispositivo nuevo

1. Antena LTE conectada (y GPS si aplica).
2. SIM de datos insertada y con plan activo (o WiFi configurado).
3. Alimentación capaz de dar picos (>2 A).
4. Firmware flasheado con `device_id`, credenciales MQTT y endpoint del broker.
5. Probar heartbeat: el dispositivo debe publicar en `jarvis/device/<id>/status`.
