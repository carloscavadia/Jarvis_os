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
// El guard del HUD mira también si hay audio sonando ahora mismo, por las tres
// vías posibles: Realtime/Kokoro (voiceActive), la cola, y el sintetizador del
// navegador.
let voiceActive = false, canSpeak = true;
const speechSynthesis = { speaking: false };
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
  if ("voiceActive" in state) voiceActive = state.voiceActive;
  if ("browserSpeaking" in state) speechSynthesis.speaking = state.browserSpeaking;
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
    busy: false, voiceEnabled: true, voiceActive: false, browserSpeaking: false,
    ...state,
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
check("audio sonando · NO abre el micrófono", attempt({ voiceActive: true }) === false);
check("sintetizador del navegador hablando · NO abre el micrófono",
      attempt({ browserSpeaking: true }) === false);
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


// ── Quién puede interrumpir a JARVIS ─────────────────────────────────────────
//
// La escucha automática se abre justo después de que JARVIS habla, así que el
// micrófono capta su propia voz desde los altavoces. Whisper la transcribe, eso
// entra como turno nuevo, y el turno nuevo cortaba la voz en curso. De ahí que
// se interrumpiera siempre. La regla: solo una acción deliberada interrumpe.

const submitSource = js.slice(
  js.indexOf('document.getElementById("form").addEventListener("submit"'),
);
const cancelsOnSubmit = /if \(!automaticTurn\) cancelSpeechQueue\(\);/.test(
  submitSource.slice(0, 400),
);
check("al enviar · solo corta si el turno NO es automático", cancelsOnSubmit);

const beginSource = js.slice(js.indexOf("function beginStreamSpeech()"));
check("al empezar a hablar · misma regla",
      /if \(!automaticTurn\) cancelSpeechQueue\(\);/.test(beginSource.slice(0, 200)));

// El motivo de la grabación tiene que sobrevivir hasta que termina.
check("la grabación recuerda si fue automática",
      /recorderWasAutomatic = automatic \|\| followUp;/.test(js)
      && /sendRecordedAudio\(blob, recorderWasAutomatic\)/.test(js));
check("y se traslada al turno",
      /async function sendRecordedAudio\(blob, automatic = false\) \{\s*\n\s*automaticTurn = automatic;/.test(js));

// Si no se reinicia, un turno automático dejaría a JARVIS sin poder ser
// interrumpido nunca más.
const finishSource = js.slice(js.indexOf("function finishStreamReply("));
check("el turno automático se cierra al terminar la respuesta",
      /automaticTurn = false;/.test(finishSource.slice(0, 1600)));

console.log(failures ? `\n${failures} fallos` : "\nEscucha continua correcta");
process.exit(failures ? 1 : 0);
