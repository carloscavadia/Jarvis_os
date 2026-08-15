// Test de regresión de la escucha continua del HUD.
//
// Ejecuta:  node clients/web-hud/tests/followup.test.js
//
// Regla que fija: la escucha se abre cuando JARVIS ha terminado de HABLAR, no
// cuando ha terminado de escribir. La cola de voz se vacía en cada pausa del
// modelo, y una pausa no es el final: armar ahí el micrófono lo dejaba abierto
// mientras JARVIS seguía hablando, se oía a sí mismo, y el turno que eso
// generaba cortaba su propia voz.
//
// Extrae la función real de index.html; no la copia.

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

const PRELUDE = `
const WebSocket = { OPEN: 1 };
let voiceEnabled = true, busy = false;
let ws = { readyState: 1 };
let replyTextComplete = true, speechQueueRunning = false, speechQueue = [];
let serverVoiceAvailable = true, canRecord = true;
let followUpListening = false, followUpTimer = null, recorder = null;
let recorderDiscard = false;
let recognition = null;
const rVoice = { textContent: "" };
function stopWakeListening() { harness.calls.push("stopWake"); }
function scheduleWakeListening() { harness.calls.push("scheduleWake"); }
function stopConversationFollowUp() { harness.calls.push("stopFollowUp"); }
function startServerRecording(opts) { harness.calls.push("record:" + JSON.stringify(opts)); }
harness.set = (state) => {
  if ("replyTextComplete" in state) replyTextComplete = state.replyTextComplete;
  if ("speechQueueRunning" in state) speechQueueRunning = state.speechQueueRunning;
  if ("speechQueue" in state) speechQueue = state.speechQueue;
  if ("busy" in state) busy = state.busy;
  if ("voiceEnabled" in state) voiceEnabled = state.voiceEnabled;
};
`;

const harness = { calls: [] };
new Function("harness", `${PRELUDE}\n${extractFunction("startConversationFollowUp")}`)(harness);

let failures = 0;
function check(label, condition, detail = "") {
  console.log((condition ? "  ok     " : "  FALLO  ") + label + (condition ? "" : `  ${detail}`));
  if (!condition) failures += 1;
}

function attempt(state) {
  harness.calls = [];
  harness.set({
    replyTextComplete: true, speechQueueRunning: false, speechQueue: [],
    busy: false, voiceEnabled: true, ...state,
  });
  harness.startConversationFollowUp();
  return harness.calls.some((call) => call.startsWith("record:"));
}

check("respuesta terminada y en silencio · abre la escucha", attempt({}) === true);

check("texto aún llegando · NO abre el micrófono",
      attempt({ replyTextComplete: false }) === false);

check("cola de voz sonando · NO abre el micrófono",
      attempt({ speechQueueRunning: true }) === false);

check("quedan frases por decir · NO abre el micrófono",
      attempt({ speechQueue: [{ text: "y una cosa más" }] }) === false);

// El caso exacto que se reportaba: el modelo hace una pausa larga, la cola se
// vacía, pero la respuesta no ha terminado.
check("pausa del modelo a mitad de respuesta · NO abre el micrófono",
      attempt({ replyTextComplete: false, speechQueue: [] }) === false);

// Y las condiciones que ya existían siguen valiendo.
check("ocupado en otro turno · NO abre el micrófono", attempt({ busy: true }) === false);
check("voz desactivada · NO abre el micrófono", attempt({ voiceEnabled: false }) === false);

// Cuando no abre por estar hablando, tampoco debe dormirse: el final de la cola
// vuelve a llamar aquí, así que reprogramar la palabra de activación sobraría.
// `attempt` reinicia el estado entero; `set` a secas arrastraría el de la
// comprobación anterior.
attempt({ replyTextComplete: false });
check("mientras habla no se duerme · el fin de la cola reintentará",
      !harness.calls.includes("scheduleWake"), harness.calls.join(","));
check("mientras habla tampoco apaga la palabra de activación",
      !harness.calls.includes("stopWake"), harness.calls.join(","));

console.log(failures ? `\n${failures} fallos` : "\nEscucha continua correcta");
process.exit(failures ? 1 : 0);
