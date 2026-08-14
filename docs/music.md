# Música en JARVIS

JARVIS puede buscar en tu biblioteca de **Navidrome** y reproducirla en el HUD.

## Configuración

Va en `.env`, no en el panel de conectores: es un servidor propio y fijo, y
registrarlo a mano cada vez que reconstruyes el contenedor no aporta nada.

```dotenv
JARVIS_NAVIDROME_URL=http://192.168.68.159:4533
JARVIS_NAVIDROME_USERNAME=tu-usuario
JARVIS_NAVIDROME_PASSWORD=tu-contrasena
```

Reconstruye el gateway y listo. Sin estas variables no se registra ninguna
herramienta de música: ofrecerle a JARVIS herramientas que siempre fallan solo
consigue que las intente y se disculpe.

## Qué puede hacer

| Herramienta | Para qué |
|---|---|
| `search_music` | Buscar por título, artista o álbum. No reproduce. |
| `play_music` | Reproducir. Con texto busca; sin texto, selección aleatoria. |
| `control_music` | Pausar, reanudar, siguiente, anterior o parar. |

**Solo lectura.** No hay ninguna acción que modifique la biblioteca.

## Cómo llega el audio al navegador

`play_music` no reproduce nada en el servidor: resuelve las canciones y devuelve
una orden para el reproductor del HUD, igual que `show_in_workspace` devuelve una
presentación para el pizarrón. Los altavoces están en el dispositivo.

```text
HUD  ──GET /music/stream/<id>?token=<clave del gateway>──▶  gateway
                                                              │
                                    credenciales Subsonic ────┘
                                                              ▼
                                                          Navidrome
```

Es un proxy y no un enlace directo por dos razones: el navegador no puede poner
cabeceras en `<audio src>`, así que la alternativa sería entregarle la contraseña
de Navidrome; y el flujo de audio no cabe en los límites de respuesta pensados
para JSON, así que se transmite por partes.

Navidrome habla el protocolo **Subsonic**: cada petición manda
`t = md5(contraseña + sal)` con una sal nueva, de forma que la contraseña nunca
viaja. Aun así, en una red que no controles conviene servir Navidrome por HTTPS.

## El reproductor

Aparece abajo a la derecha **solo mientras suena algo**. Baja de volumen cuando
JARVIS habla y lo recupera al terminar, así que no hace falta pausar para
preguntarle algo.

## Diagnóstico

| Síntoma | Causa probable |
|---|---|
| «No puedo reproducir audio» | Faltan las variables `JARVIS_NAVIDROME_*` o no se reconstruyó el gateway |
| «El servidor de música rechazó la consulta» | Usuario o contraseña incorrectos; el mensaje incluye el motivo de Navidrome |
| «No pude contactar el servidor de música» | URL o red; comprueba que el gateway alcanza esa IP y puerto |
| El reproductor no aparece | El navegador bloqueó la reproducción automática: interactúa con la página una vez |
