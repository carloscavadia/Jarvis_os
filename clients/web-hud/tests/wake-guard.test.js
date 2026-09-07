// Test de la guarda del micrófono de activación.
//
// El caso real: JARVIS se cortaba a sí mismo a media respuesta. La causa no era
// el detector ni el eco, sino que la guarda estaba duplicada en dos sitios y las
// copias habían divergido: `scheduleWakeListening` miraba `speechQueueRunning` y
// `startWakeListening` no.
//
// Entre dos fragmentos de voz `voiceActive` vuelve a `false` mientras la cola
// sigue hablando. En esa rendija el micrófono se abría, JARVIS se oía por el
// altavoz, disparaba su propia palabra de activación, y la conversación nueva
// cancelaba la voz en curso.
//
// Extrae la función real de index.html; no la copia.

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
  return js.slice(match.index, index + 1);
}

// Un entorno mínimo con las banderas que la guarda consulta.
function montar(estado = {}) {
  const base = {
    wakeAvailable: true, canRecord: true,
    ws: { readyState: 1 },
    busy: false, voiceActive: false, speechQueueRunning: false,
    followUpListening: false, recorder: null,
  };
  const v = { ...base, ...estado };
  const cuerpo = [
    "const WebSocket = { OPEN: 1 };",
    ...Object.entries(v).map(([k, val]) => `let ${k} = ${JSON.stringify(val)};`),
    extractFunction("wakeBlocked"),
    "return wakeBlocked();",
  ].join("\n");
  return new Function(cuerpo)();
}

function prueba(nombre, fn) {
  try { fn(); console.log(`ok - ${nombre}`); }
  catch (error) { console.error(`FALLO - ${nombre}`); throw error; }
}

prueba("con todo en calma, el micrófono puede abrirse", () => {
  assert.equal(montar(), false);
});

prueba("la cola de voz bloquea el micrófono aunque voiceActive sea false", () => {
  // Este es el caso que cortaba a JARVIS: entre dos fragmentos.
  assert.equal(montar({ voiceActive: false, speechQueueRunning: true }), true);
});

prueba("hablando en este instante también bloquea", () => {
  assert.equal(montar({ voiceActive: true }), true);
});

prueba("un turno en curso bloquea", () => {
  assert.equal(montar({ busy: true }), true);
});

prueba("la escucha de seguimiento bloquea", () => {
  assert.equal(montar({ followUpListening: true }), true);
});

prueba("grabando ya, no se abre otra vez", () => {
  assert.equal(montar({ recorder: { state: "recording" } }), true);
});

prueba("sin socket abierto no se escucha", () => {
  assert.equal(montar({ ws: { readyState: 3 } }), true);
  assert.equal(montar({ ws: null }), true);
});

prueba("sin permiso de micrófono no se escucha", () => {
  assert.equal(montar({ canRecord: false }), true);
});

prueba("las dos entradas comparten la misma guarda", () => {
  // La divergencia entre las dos copias fue la causa. Si alguien vuelve a
  // escribir la condición a mano en una de ellas, esto lo caza.
  for (const fn of ["scheduleWakeListening", "startWakeListening"]) {
    assert.match(extractFunction(fn), /wakeBlocked\(\)/,
      `${fn} debe usar wakeBlocked(), no su propia copia de la condición`);
  }
});
