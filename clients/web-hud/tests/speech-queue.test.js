// Test de regresión de la cola de voz del HUD.
//
// Ejecuta:  node clients/web-hud/tests/speech-queue.test.js
//
// Extrae las funciones reales de index.html y las ejercita con un entorno
// mínimo. No copia el código: si alguien cambia la lógica en el HUD, este test
// ejecuta la versión nueva.
//
// Cubre el fallo por el que JARVIS se cortaba a mitad de frase: cancelar el
// audio en curso es correcto cuando el usuario interrumpe, pero no cuando somos
// nosotros los que cambiamos de idea sobre qué decir.

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
  const start = index;
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
  "flushStreamSpeech",
  "finishStreamSpeech",
  "dropQueuedSpeech",
  "cancelSpeechQueue",
];

// Estado y colaboradores que las funciones esperan encontrar a su alrededor.
const PRELUDE = `
let speechQueue = [], speechSession = 0, streamSpeechBuffer = "", streamSpeechSource = "";
let streamSpeechStarted = false, voiceEnabled = true, voiceActive = false;
let realtimeVoiceAvailable = false, serverVoiceAvailable = true, canSpeak = false;
let stopCurrentAudio = null, voiceAnalyser = null, voiceWaveform = null;
const speechSynthesis = { cancel() {} };
function closeRealtimeVoiceSession() {}
function enqueueSpeech(text) { if (text.trim()) harness.spoken.push(text.trim()); }
function speak(text) { harness.spoken.push(text); }
`;

const harness = { spoken: [], hardCuts: 0 };
const build = new Function(
  "harness",
  `${PRELUDE}
   ${NAMES.map(extractFunction).join("\n")}
   harness.reset = () => {
     speechQueue = []; streamSpeechBuffer = ""; streamSpeechSource = "";
     streamSpeechStarted = false; harness.spoken = []; harness.hardCuts = 0;
     // Simula que hay una frase sonando ahora mismo.
     stopCurrentAudio = () => { harness.hardCuts += 1; stopCurrentAudio = null; };
   };
   harness.stream = (text) => {
     streamSpeechSource += text; streamSpeechBuffer += text;
     harness.flushStreamSpeech(false);
   };`
);
build(harness);

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  ok     ${label}`);
    return;
  }
  failures += 1;
  console.log(`  FALLO  ${label}${detail ? `  ${detail}` : ""}`);
}

// ── El texto final coincide con lo que ya se dijo ────────────────────────────
harness.reset();
harness.stream("Todo en orden, jefe. ");
harness.stream("La temperatura es de 22 grados.");
harness.finishStreamSpeech("Todo en orden, jefe. La temperatura es de 22 grados.");
check("texto final idéntico · no corta el audio en curso", harness.hardCuts === 0);
check(
  "texto final idéntico · no repite frases",
  harness.spoken.join(" ") === "Todo en orden, jefe. La temperatura es de 22 grados.",
  JSON.stringify(harness.spoken)
);

// ── El texto final añade lo que faltaba (el caso frecuente) ──────────────────
harness.reset();
harness.stream("Todo en orden, jefe.");
harness.finishStreamSpeech("Todo en orden, jefe. Nada más que reportar.");
check("texto final más largo · no corta", harness.hardCuts === 0);
check(
  "texto final más largo · dice solo lo nuevo",
  harness.spoken.join(" ") === "Todo en orden, jefe. Nada más que reportar.",
  JSON.stringify(harness.spoken)
);

// ── El detalle se va al pizarrón y la voz pasa al resumen ────────────────────
harness.reset();
harness.stream("Aquí tienes la tabla completa con todos los datos. ");
harness.dropQueuedSpeech();
harness.finishStreamSpeech("He dejado el detalle en el pizarrón.");
check("pizarrón · no corta la frase en curso", harness.hardCuts === 0);
check(
  "pizarrón · dice el resumen",
  harness.spoken.join(" ").includes("He dejado el detalle en el pizarrón"),
  JSON.stringify(harness.spoken)
);

// ── El cierre puede llegar dos veces (reply_delta y reply) ───────────────────
harness.reset();
harness.stream("Listo.");
harness.finishStreamSpeech("Listo.");
const afterFirst = harness.spoken.length;
harness.finishStreamSpeech("Listo.");
check("doble cierre · no repite", harness.spoken.length === afterFirst, JSON.stringify(harness.spoken));
check("doble cierre · no corta", harness.hardCuts === 0);

// ── La cancelación dura debe seguir cortando de verdad ───────────────────────
harness.reset();
harness.stream("Estoy diciendo algo largo.");
harness.cancelSpeechQueue();
check("interrupción real · sí corta el audio", harness.hardCuts === 1);

console.log(failures ? `\n${failures} fallo(s)` : "\nCola de voz correcta");
process.exit(failures ? 1 : 0);
