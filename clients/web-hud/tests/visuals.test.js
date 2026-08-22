// Test del lenguaje visual del HUD.
//
// El orbe reaccionaba a los eventos, pero siempre igual: un mismo anillo para
// ejecutar una herramienta, terminarla o que fuera denegada. Y el fondo era un
// relleno plano: el orbe se leía como un objeto pegado sobre negro, no como
// algo que habita un sitio.
//
// Extrae las constantes reales de index.html; no las copia.

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const js = html.split("<script>")[1].split("</script>")[0];

function extractConst(name) {
  const match = new RegExp(`\\n  const ${name} = [\\s\\S]*?\\n  \\};?\\n`).exec(js)
    || new RegExp(`\\n  const ${name} = [\\s\\S]*?\\n  \\];?\\n`).exec(js);
  if (!match) throw new Error(`No encuentro la constante ${name}`);
  return match[0];
}

const H = {};
new Function("H", [
  extractConst("PULSE_KINDS"),
  extractConst("FIELD_LAYERS"),
  "H.PULSE_KINDS = PULSE_KINDS; H.FIELD_LAYERS = FIELD_LAYERS;",
].join("\n"))(H);

const { PULSE_KINDS, FIELD_LAYERS } = H;

function prueba(nombre, fn) {
  fn();
  console.log(`  ok  ${nombre}`);
}

prueba("cada fase de una acción tiene su propia firma", () => {
  for (const fase of ["running", "completed", "denied", "awaiting", "generic"]) {
    assert.ok(PULSE_KINDS[fase], `falta la firma de ${fase}`);
  }
});

prueba("lo denegado va hacia dentro y en rojo, no como el resto", () => {
  assert.ok(PULSE_KINDS.denied.speed < 0, "un rechazo que se expande se lee como éxito");
  assert.ok(PULSE_KINDS.denied.hue.startsWith("255,"), "debe distinguirse por color");
});

prueba("completar es más brillante y más breve que estar en marcha", () => {
  assert.ok(PULSE_KINDS.completed.alpha > PULSE_KINDS.running.alpha);
  assert.ok(PULSE_KINDS.completed.fade > PULSE_KINDS.running.fade, "un chasquido, no una estela");
  assert.ok(PULSE_KINDS.completed.speed > PULSE_KINDS.running.speed);
});

prueba("esperar tu decisión se sostiene en el aire", () => {
  // Si se desvaneciera como los demás, la espera dejaría de verse.
  assert.ok(PULSE_KINDS.awaiting.fade < PULSE_KINDS.running.fade);
  assert.ok(PULSE_KINDS.awaiting.hue, "el ámbar es lo que lo hace reconocible");
});

prueba("el campo tiene capas a distinta profundidad", () => {
  assert.ok(FIELD_LAYERS.length >= 3, "sin varias capas no hay paralaje");
  const profundidades = FIELD_LAYERS.map(l => l.depth);
  assert.deepStrictEqual(
    [...profundidades].sort((a, b) => a - b), profundidades,
    "las capas deben ir de cerca a lejos",
  );
});

prueba("lo lejano se ve menos que lo cercano", () => {
  for (let i = 1; i < FIELD_LAYERS.length; i++) {
    assert.ok(
      FIELD_LAYERS[i].alpha < FIELD_LAYERS[i - 1].alpha,
      "una capa de fondo tan brillante como la de delante aplana la escena",
    );
    assert.ok(FIELD_LAYERS[i].size < FIELD_LAYERS[i - 1].size);
  }
});

prueba("la densidad es por área, no un número fijo", () => {
  // Un recuento absoluto se pierde en un monitor y satura una tablet.
  for (const capa of FIELD_LAYERS) {
    assert.ok(capa.per_mpx > 0, "falta la densidad por megapíxel");
    assert.strictEqual(capa.count, undefined, "quedó un recuento absoluto");
  }
});

prueba("el campo se omite en equipos lentos", () => {
  // El kiosko va sobre Raspberry: el campo entero se salta ahí.
  assert.ok(/function seedField\(\)[\s\S]*?if \(isLowSpec\) return;/.test(js));
});

prueba("la voz se lee por espectro, no solo por volumen", () => {
  assert.ok(/getByteFrequencyData\(voiceSpectrum\)/.test(js), "seguiría siendo un único número");
  assert.ok(/spectrumSmooth/.test(js), "sin suavizado el anillo tirita");
});

console.log("\nLenguaje visual correcto");
