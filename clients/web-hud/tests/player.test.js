// Test de regresión del reproductor de música del HUD.
//
// Ejecuta:  node clients/web-hud/tests/player.test.js
//
// Regla que fija: el panel solo es visible mientras hay algo sonando. Mostrarlo
// al pedir la reproducción —en vez de al empezar— dejaba un reproductor vacío
// en pantalla cuando la pista fallaba o el navegador bloqueaba el audio.
//
// Extrae las funciones reales de index.html; no las copia.

const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const js = html.split("<script>")[1].split("</script>")[0];

function extractFunction(name) {
  const header = new RegExp(`\\n  (?:async )?function ${name}\\([^)]*\\)\\s*\\{`);
  const match = header.exec(js);
  if (!match) throw new Error(`No encuentro la función ${name} en index.html`);
  let depth = 0;
  // Se empieza en la llave que cierra la cabecera, no en la primera del texto:
  // una firma como `stopMusic({ hide = true } = {})` trae llaves en los
  // parámetros y contarlas desde ahí trunca la función.
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

const NAMES = [
  "formatTime",
  "musicMediaUrl",
  "renderNowPlaying",
  "stopMusic",
  "playMusicAt",
  "handleMusicCommand",
];

// Elementos y colaboradores mínimos. `Audio` no reproduce: expone play/pause y
// deja que el test dispare onplay/onerror a mano.
const PRELUDE = `
function fakeEl() {
  return { textContent: "", hidden: true, src: "", style: {},
           classList: { set: new Set(),
             add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
             contains(c) { return this.set.has(c); } } };
}
const playerEl = fakeEl(), playerTitle = fakeEl(), playerArtist = fakeEl();
const playerCover = fakeEl(), playerQueueEl = fakeEl(), playerProgress = fakeEl();
const playerTime = fakeEl(), playerToggle = fakeEl();
let musicAudio = null, musicQueue = [], musicIndex = 0;
let voiceActive = false, speechQueueRunning = false, voiceSocket = null;
const tokenInput = { value: "clave" };
function hubKey() { return tokenInput.value; }
function gatewayHttpBase() { return harness.base; }
function duckedForSpeech() { return false; }
function addLine(who, text) { harness.lines.push(text); }
class Audio {
  constructor(src) { this.src = src; this.volume = 1; this.paused = true;
    harness.created.push(this); }
  play() {
    if (harness.playRejects) return Promise.reject(new Error("bloqueado"));
    this.paused = false;
    // El navegador dispara onplay de forma asíncrona; aquí basta con hacerlo ya.
    if (this.onplay) this.onplay();
    return Promise.resolve();
  }
  pause() { this.paused = true; }
}
`;

const harness = { lines: [], created: [], base: "http://gw:8080", playRejects: false };
new Function(
  "harness",
  `${PRELUDE}
   ${NAMES.map(extractFunction).join("\n")}
   harness.visible = () => playerEl.classList.contains("active");
   harness.setQueue = (queue) => { musicQueue = queue; musicIndex = 0; };
   harness.progress = () => playerProgress.style.width;
   harness.reset = () => {
     musicAudio = null; musicQueue = []; musicIndex = 0;
     playerEl.classList.remove("active");
     harness.lines = []; harness.created = []; harness.playRejects = false;
   };`
)(harness);

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  ok     ${label}`);
    return;
  }
  failures += 1;
  console.log(`  FALLO  ${label}${detail ? `  ${detail}` : ""}`);
}

const QUEUE = [
  { id: "1", title: "Uno", artist: "A" },
  { id: "2", title: "Dos", artist: "B" },
];

async function main() {
// ── Visibilidad ─────────────────────────────────────────────────────────────
  harness.reset();
  check("arranca oculto", !harness.visible());

  harness.handleMusicCommand({ command: "play", connector: "musica", queue: QUEUE });
  check("suena algo · se muestra", harness.visible());

  // La cola termina sola: nada sonando, nada que mostrar.
  harness.created.at(-1).onended();       // pasa a la 2ª
  harness.created.at(-1).onended();       // se acaba la cola
  check("cola terminada · se oculta", !harness.visible());

  // ── El navegador bloquea la reproducción ────────────────────────────────────
  harness.reset();
  harness.playRejects = true;
  harness.handleMusicCommand({ command: "play", connector: "musica", queue: QUEUE });
  await new Promise((resolve) => setTimeout(resolve, 0));  // deja correr el catch
  check("reproducción bloqueada · no queda visible", !harness.visible());

  // ── Sin URL utilizable ──────────────────────────────────────────────────────
  harness.reset();
  harness.base = "";
  harness.handleMusicCommand({ command: "play", connector: "musica", queue: QUEUE });
  check("sin URL · no queda visible", !harness.visible());
  harness.base = "http://gw:8080";

  // ── Pausa: sigue visible para poder reanudar ────────────────────────────────
  harness.reset();
  harness.handleMusicCommand({ command: "play", connector: "musica", queue: QUEUE });
  harness.handleMusicCommand({ command: "pause" });
  check("en pausa · sigue visible (hay que poder reanudar)", harness.visible());
  harness.handleMusicCommand({ command: "stop" });
  check("stop · se oculta", !harness.visible());

  // ── Un elemento descartado no mueve nada ────────────────────────────────────
  harness.reset();
  harness.handleMusicCommand({ command: "play", connector: "musica", queue: QUEUE });
  const abandoned = harness.created.at(-1);
  harness.handleMusicCommand({ command: "next" });
  const current = harness.created.at(-1);
  check("cambiar de pista crea otro audio", abandoned !== current);
  abandoned.ontimeupdate?.();
  abandoned.onended?.();
  check("el audio abandonado no avanza la cola", harness.visible());

  // ── La URL lleva la clave del gateway y el módulo ───────────────────────────
  harness.reset();
  harness.handleMusicCommand({ command: "play", connector: "musica", queue: QUEUE });
  const url = harness.created.at(-1).src;
  check("la URL apunta al proxy del gateway", url.includes("/music/stream/1"), url);
  check("la URL lleva la clave", url.includes("token=clave"), url);

}

main().then(() => {
  console.log(failures ? `\n${failures} fallo(s)` : "\nReproductor correcto");
  process.exit(failures ? 1 : 0);
});
