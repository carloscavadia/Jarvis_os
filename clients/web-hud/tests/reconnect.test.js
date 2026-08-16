// Test de regresión de la reconexión del HUD.
//
// Ejecuta:  node clients/web-hud/tests/reconnect.test.js
//
// Regla que fija: un enlace caído se reintenta solo. Sin esto, el primer intento
// fallido dejaba el HUD en modo demo hasta que alguien recargaba la página a
// mano — y abrir el HUD antes de que el gateway termine de arrancar es
// exactamente lo que pasa en un kiosko que enciende con el servidor.
//
// La excepción es una clave rechazada (1008): reintentar no la arregla, sólo
// taparía el aviso con ruido.
//
// Extrae las funciones reales de index.html; no las copia.

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const js = html.split("<script>")[1].split("</script>")[0];

function extractFunction(name) {
  const header = new RegExp(`\\n  (?:async )?function ${name}\\([^)]*\\)\\s*\\{`);
  const match = header.exec(js);
  if (!match) throw new Error(`No encuentro la función ${name} en index.html`);
  let depth = 0;
  let index = match.index + match[0].length - 1;
  for (;;) {
    if (js[index] === "{") depth += 1;
    else if (js[index] === "}") depth -= 1;
    if (depth === 0) break;
    index += 1;
    if (index >= js.length) throw new Error(`Función ${name} sin cerrar`);
  }
  return js.slice(match.index, index + 1) + `\nharness.${name} = ${name};`;
}

const NAMES = ["reconnectDelay", "shouldReconnect", "closeLabel", "sameHost", "servedByGateway", "defaultWsUrl"];
const harness = {};
new Function("harness", "location", NAMES.map(extractFunction).join("\n"))(
  harness,
  { protocol: "http:", host: "x", hostname: "x", port: "", pathname: "/" },
);

const { reconnectDelay, shouldReconnect, closeLabel, sameHost } = harness;

// ── La espera entre intentos ────────────────────────────────────────────────

// Arranca corta: lo normal es que el gateway esté a segundos de estar listo, y
// ahí es donde se nota la diferencia entre reconectar y quedarse en demo.
assert.ok(reconnectDelay(0) <= 1000, "el primer reintento debe ser casi inmediato");

// Y crece, para no martillear un servidor que está caído de verdad.
assert.ok(reconnectDelay(1) > reconnectDelay(0), "la espera debe crecer");
assert.ok(reconnectDelay(4) > reconnectDelay(2), "la espera debe seguir creciendo");

// Pero con techo: un HUD que espera minutos ya no está reconectando.
const techo = reconnectDelay(50);
assert.ok(techo <= 30000, `la espera no puede dispararse (era ${techo} ms)`);
assert.strictEqual(reconnectDelay(50), reconnectDelay(10), "por encima del techo, constante");

// ── Cuándo NO reintentar ────────────────────────────────────────────────────

// 1008 es la clave rechazada. Reintentar no la corrige y taparía el aviso.
assert.strictEqual(shouldReconnect(1008), false, "una clave rechazada no se reintenta");

// Todo lo demás sí: 1006 es el cierre anómalo del navegador cuando el servidor
// no está escuchando, que es justo el caso del arranque en frío.
assert.strictEqual(shouldReconnect(1006), true, "un servidor aún no levantado se reintenta");
assert.strictEqual(shouldReconnect(1001), true, "un gateway que se reinicia se reintenta");
assert.strictEqual(shouldReconnect(1000), true, "un cierre limpio del servidor se reintenta");

// ── Qué se le dice a la persona al caerse ───────────────────────────────────

// Un 1011 es el gateway aceptando la clave y reventando al preparar la sesión.
// Verlo como "CLAVE INVÁLIDA" manda a buscar el fallo al sitio equivocado: pasó
// de verdad, y costó revisar una y otra vez una credencial que estaba bien.
assert.notStrictEqual(closeLabel(1011), closeLabel(1008),
  "un error del servidor no puede leerse igual que una clave rechazada");
assert.strictEqual(closeLabel(1008), "CLAVE INVÁLIDA");
assert.strictEqual(closeLabel(1006), "DESCONECTADO");

// ── La URL que se adivina cuando nadie la ha escrito ────────────────────────

function urlDesde(loc) {
  const h = {};
  new Function("harness", "location",
    extractFunction("servedByGateway") + "\n" + extractFunction("defaultWsUrl"))(h, loc);
  return h.defaultWsUrl();
}

// Servido por el propio gateway: mismo host y puerto, sin adivinar nada.
assert.strictEqual(
  urlDesde({ protocol: "http:", host: "192.168.68.100:8080", hostname: "192.168.68.100", port: "8080", pathname: "/hud" }),
  "ws://192.168.68.100:8080/ws/hub-test",
);

// Detrás de un proxy en el 80: sin puerto en la URL, el WebSocket pasa por el
// proxy igual. Añadir :8080 a mano lo mandaría a un puerto que no tiene por qué
// estar expuesto.
assert.strictEqual(
  urlDesde({ protocol: "http:", host: "jarvis.local", hostname: "jarvis.local", port: "", pathname: "/" }),
  "ws://jarvis.local/ws/hub-test",
);

// En HTTPS el esquema tiene que ser wss: un ws:// desde una página segura lo
// bloquea el navegador sin explicación visible.
assert.strictEqual(
  urlDesde({ protocol: "https:", host: "jarvis.example", hostname: "jarvis.example", port: "", pathname: "/" }),
  "wss://jarvis.example/ws/hub-test",
);

// ── Que el HUD no acabe hablando con dos origenes ───────────────────────────
//
// Abriendo por el tunel (127.0.0.1:8080) con una URL de red guardada de antes
// (192.168.68.100:8080), el WebSocket seguia conectando —no pasa por CORS— pero
// cada fetch salia al otro origen y el navegador lo bloqueaba. Paso de verdad:
// el enlace decia EN LINEA y las tareas, Proxmox y el estado del servidor
// fallaban en silencio.

assert.strictEqual(
  sameHost("ws://192.168.68.100:8080/ws/hub-test", "127.0.0.1:8080"), false,
  "una URL guardada de otro host debe detectarse como obsoleta",
);
assert.strictEqual(
  sameHost("ws://127.0.0.1:8080/ws/hub-test", "127.0.0.1:8080"), true,
  "la misma maquina y puerto es el mismo destino aunque la ruta difiera",
);
// El puerto forma parte del destino: mismo host y distinto puerto es otro sitio.
assert.strictEqual(sameHost("ws://127.0.0.1:9000/ws/x", "127.0.0.1:8080"), false);
// Una URL rota no puede hacerse pasar por valida.
assert.strictEqual(sameHost("no-es-una-url", "127.0.0.1:8080"), false);

console.log("reconnect.test.js OK");

// ── De dónde salen las URLs HTTP ────────────────────────────────────────────

function baseDesde(loc, wsGuardada) {
  const h = {};
  new Function(
    "harness", "location", "resolvedWsUrl",
    extractFunction("servedByGateway") + "\n" + extractFunction("gatewayHttpBase"),
  )(h, loc, () => wsGuardada);
  return h.gatewayHttpBase();
}

// Servido por el gateway: siempre su propio origen. Aunque haya guardada una URL
// de otro host, las peticiones van al servidor que tenemos delante y no cruzan
// ningún límite de origen.
assert.strictEqual(
  baseDesde(
    { protocol: "http:", host: "127.0.0.1:8080", hostname: "127.0.0.1", port: "8080",
      pathname: "/hud", origin: "http://127.0.0.1:8080" },
    "ws://192.168.68.100:8080/ws/hub-test",
  ),
  "http://127.0.0.1:8080",
  "el HUD servido por el gateway debe pedir a su propio origen",
);

// Abierto como fichero suelto, no hay origen del que fiarse: se deriva del
// WebSocket, que es lo único que apunta al gateway.
assert.strictEqual(
  baseDesde(
    { protocol: "file:", host: "", hostname: "", port: "", pathname: "/index.html", origin: "null" },
    "ws://192.168.68.100:8080/ws/hub-test",
  ),
  "http://192.168.68.100:8080",
);

// Y en HTTPS el esquema derivado tiene que ser https, no http.
assert.strictEqual(
  baseDesde(
    { protocol: "file:", host: "", hostname: "", port: "", pathname: "/index.html", origin: "null" },
    "wss://jarvis.example/ws/hub-test",
  ),
  "https://jarvis.example",
);

console.log("gatewayHttpBase OK");
