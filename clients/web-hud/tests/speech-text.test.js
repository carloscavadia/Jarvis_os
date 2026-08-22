// Test del texto que se manda al sintetizador.
//
// Ejecuta:  node clients/web-hud/tests/speech-text.test.js
//
// `prepareSpeechText` se llamaba en dos sitios del HUD y **no existía**: solo
// estaba la versión del servidor (`prepare_speech_text`, gateway/voice.py).
// Cada respuesta lanzaba un ReferenceError dentro de `enqueueSpeech`, y como
// `finishStreamReply` invoca la voz antes de su `setBusy(false)`, el turno moría
// ahí: sin voz, sin cerrar la conversación y sin devolver el teclado.
//
// No lo veía nadie: `node --check` solo valida sintaxis, y el test de la cola de
// voz sustituye `enqueueSpeech` entera por un doble, así que jamás ejecutaba la
// función real. Por eso aquí se extraen las dos de verdad.

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

function extractConst(name) {
  const match = new RegExp(`\\n  const ${name} = [\\s\\S]*?;\\n`).exec(js);
  if (!match) throw new Error(`No encuentro la constante ${name}`);
  return match[0];
}

const H = {};
new Function("H", [
  extractConst("SPEECH_ENTITIES"),
  extractFunction("prepareSpeechText"),
  "H.prepareSpeechText = prepareSpeechText;",
].join("\n"))(H);

const { prepareSpeechText } = H;

function prueba(nombre, fn) {
  fn();
  console.log(`  ok  ${nombre}`);
}

prueba("la función existe y devuelve texto", () => {
  assert.strictEqual(typeof prepareSpeechText, "function");
  assert.strictEqual(prepareSpeechText("Hola, jefe."), "Hola, jefe.");
});

prueba("no se leen los bloques de código en voz alta", () => {
  const dicho = prepareSpeechText("Listo:\n```python\nprint('hola')\n```\nYa está.");
  assert.ok(!dicho.includes("print"), dicho);
  assert.ok(dicho.includes("Listo") && dicho.includes("Ya está"));
});

prueba("de un enlace se lee el texto, no la URL", () => {
  assert.strictEqual(
    prepareSpeechText("Mira la [documentación](https://example.com/muy/larga)."),
    "Mira la documentación.",
  );
  assert.strictEqual(prepareSpeechText("Ve a https://example.com/x ahora"), "Ve a ahora");
});

prueba("las viñetas y los títulos pierden su marca", () => {
  assert.strictEqual(
    prepareSpeechText("## Resumen\n- Primero\n- Segundo\n1. Tercero"),
    "Resumen Primero Segundo Tercero",
  );
});

prueba("una tabla se lee con pausas, no con barras", () => {
  const dicho = prepareSpeechText("| Luz | on |\n| --- | --- |\n| Salón | on |");
  assert.ok(!dicho.includes("|"), dicho);
  assert.ok(!dicho.includes("---"), dicho);
  assert.ok(dicho.includes("Luz") && dicho.includes("Salón"));
});

prueba("se quitan el énfasis y las entidades HTML", () => {
  assert.strictEqual(prepareSpeechText("Est**á** *on* y `listo`"), "Está on y listo");
  assert.strictEqual(prepareSpeechText("Ba&ntilde; &amp; caf&#233;"), "Ba&ntilde; & café");
});

prueba("no deja espacios antes de la puntuación", () => {
  assert.strictEqual(prepareSpeechText("Hecho  ,  jefe .  "), "Hecho, jefe.");
});

prueba("aguanta lo vacío y lo nulo sin romperse", () => {
  for (const entrada of ["", "   ", null, undefined]) {
    assert.strictEqual(prepareSpeechText(entrada), "");
  }
});

// La regresión de verdad: `enqueueSpeech` es quien la llamaba. Se ejecuta la
// función REAL —no un doble como en speech-queue.test.js— para que cualquier
// dependencia que falte vuelva a fallar aquí en vez de en el navegador.
prueba("enqueueSpeech real se ejecuta sin dependencias que falten", () => {
  const G = { encolado: [] };
  new Function("G", [
    "let voiceEnabled = true, realtimeVoiceAvailable = false, serverVoiceAvailable = true;",
    "let speechSession = 1;",
    "const speechQueue = { push: (item) => G.encolado.push(item) };",
    "const rVoice = { textContent: '' };",
    "function speakInBrowser(){} function runSpeechQueue(){}",
    "function requestSpeechAudio(text){ return Promise.resolve(text); }",
    extractConst("SPEECH_ENTITIES"),
    extractFunction("prepareSpeechText"),
    extractFunction("enqueueSpeech"),
    "enqueueSpeech('Encendí la **luz** del [salón](https://home/x).');",
  ].join("\n"))(G);

  assert.strictEqual(G.encolado.length, 1);
  assert.strictEqual(G.encolado[0].text, "Encendí la luz del salón.");
});

console.log("\nTexto para la voz correcto");
