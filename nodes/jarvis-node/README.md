# jarvis-node

El agente compañero de JARVIS_OS: le da **manos en una máquina concreta** —tu Windows,
un portátil, un servidor— sin que esa máquina abra un solo puerto.

## Por qué existe

`run_shell` corre donde corre el cerebro: el contenedor LXC de Proxmox. Para que JARVIS
haga algo en tu PC de escritorio hace falta software *en ese PC*. Eso es este nodo.

Arquitectónicamente es **otro dispositivo**, como un ESP32: usa el mismo transporte MQTT
y su propio espacio de topics. No hubo que inventar un canal nuevo.

## Cómo se protege

**Solo conexiones salientes.** El nodo se conecta al broker y escucha por ahí. Tu PC
detrás del router no necesita redirección de puertos, y una máquina que no escucha no
tiene puerta que forzar.

**La política vive en el nodo.** El manifiesto lo publica la máquina, no lo elige el
gateway. Si alguien compromete el cerebro —o el modelo se traga una inyección desde un
correo—, el nodo sigue negándose a lo que su dueño no declaró.

**Operaciones tipadas, no cadenas de shell.** `run_command` recibe `argv` como lista y
se ejecuta sin intérprete: un `|` o un `&&` llegan como argumento literal del programa.

**Solo lectura de fábrica.** Escribir o ejecutar exige activarlo a mano. Un descuido al
configurar deja un nodo inútil, nunca uno peligroso.

**Prohibiciones absolutas.** `dd`, `mkfs`, `shutdown`, `diskpart`… se rechazan aunque se
declaren en la configuración. Las rutas `.ssh`, `.aws`, `.gnupg` y `.env` no se leen ni
se escriben aunque caigan dentro de una raíz permitida.

## Instalación

```bash
pip install -e "nodes/jarvis-node[mqtt,processes]"
```

## Configuración

En `~/.jarvis-node.json`. Un fichero mínimo da un nodo que solo sabe decir quién es:

```json
{ "node_id": "pc-carlos" }
```

Uno útil declara explícitamente su alcance:

```json
{
  "node_id": "pc-carlos",
  "read_roots":  ["~/Proyectos", "~/Documentos"],
  "write_roots": ["~/Proyectos/borrador"],
  "allow_writes": true,
  "executables": ["git", "python", "node"],
  "allow_process_control": false,
  "command_timeout": 30
}
```

Comprueba qué estás a punto de conceder **antes** de conceder nada:

```bash
jarvis-node --check
```

Imprime el manifiesto y sale sin conectar.

## Arranque

```bash
export JARVIS_NODE_BROKER=broker.tucasa.com
export JARVIS_NODE_USER=pc-carlos
export JARVIS_NODE_PASSWORD=...
jarvis-node
```

TLS va activado por defecto. `--no-tls` existe solo para pruebas en local.

## Operaciones

| Operación | Requiere | Qué hace |
|---|---|---|
| `system_info` | nada | Identidad y salud de la máquina |
| `list_directory` | `read_roots` | Lista un directorio |
| `read_file` | `read_roots` | Lee un archivo, recortado |
| `write_file` | `allow_writes` + `write_roots` | Escribe un archivo |
| `run_command` | `executables` | Ejecuta un programa con `argv` |
| `list_processes` | `allow_process_control` | Inventario de procesos |

## Topics

```
jarvis/node/{node_id}/manifest   → qué ofrece este nodo (retenido)
jarvis/node/{node_id}/request    ← el gateway pide
jarvis/node/{node_id}/response   → el nodo contesta
jarvis/node/{node_id}/status     → online / offline (con testamento MQTT)
```

## Estado

El nodo está completo y probado (38 tests). **Todavía no hay herramientas en el cerebro
que lo usen**: el siguiente paso es el lado del gateway —descubrir nodos por su
manifiesto y exponer `node_run` / `node_read` como herramientas del agente, pasando por
el motor de políticas y la auditoría que ya existen en `core/jarvis_core/policy/`.
