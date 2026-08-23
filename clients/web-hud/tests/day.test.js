// Test de la vista de día: lo que ocurre y lo que vence, en la misma línea.
//
// Ejecuta:  node clients/web-hud/tests/day.test.js
//
// El problema que resuelve: el calendario y el gestor de tareas eran dos
// mitades que no se hablaban. Una tarea tiene fecha de EJECUCIÓN («cuándo actúa
// JARVIS») y ahora también de ENTREGA («cuándo tiene que estar hecho»), que son
// cosas distintas. Lo que se fija aquí es que no se confunden.
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

const harness = {};
new Function("harness", [
  extractFunction("dayTimeline"),
  extractFunction("formatTaskTime"),
].join("\n"))(harness);
const { dayTimeline, formatTaskTime } = harness;

const DIA = 1_800_000_000;          // inicio de un día cualquiera
const FIN = DIA + 86400;
const h = horas => DIA + horas * 3600;

// ── Las tres cosas conviven y se distinguen ──────────────────────────────────

{
  const r = dayTimeline(DIA, FIN, {
    events: [{ starts_at: h(10), ends_at: h(11), title: "Médico del niño", location: "Clínica", all_day: false }],
    deliverables: [{ starts_at: h(18), task_id: 7, title: "Informe", overdue: false, status: "pending", priority: "high" }],
    tasks: [{ id: 9, enabled: true, next_run: h(8), title: "Aviso de riego" }],
  });

  assert.strictEqual(r.total, 3);
  assert.deepStrictEqual(r.items.map(i => i.kind), ["run", "event", "due"], "tienen que salir por hora");
  assert.strictEqual(r.items[1].meta, "Clínica");
  assert.strictEqual(r.items[2].taskId, 7);
}

// ── La fecha de entrega no es la de ejecución ───────────────────────────────

{
  // Una tarea que se ejecuta HOY pero vence otro día: hoy sale como aviso,
  // no como entrega. Confundirlas haría que el calendario mostrase los avisos
  // en lugar de los plazos, que es lo que pasaba antes.
  const r = dayTimeline(DIA, FIN, {
    deliverables: [],
    tasks: [{ id: 3, enabled: true, next_run: h(9), title: "Informe" }],
  });
  assert.deepStrictEqual(r.items.map(i => i.kind), ["run"]);
}

{
  // Y al revés: vence hoy, se avisó ayer. Hoy sale como entrega.
  const r = dayTimeline(DIA, FIN, {
    deliverables: [{ starts_at: h(12), task_id: 3, title: "Informe", overdue: false }],
    tasks: [{ id: 3, enabled: true, next_run: DIA - 3600, title: "Informe" }],
  });
  assert.deepStrictEqual(r.items.map(i => i.kind), ["due"]);
}

{
  // Si el aviso y la entrega caen el MISMO día, no se pinta dos veces: sería la
  // misma cosa duplicada en la misma pantalla.
  const r = dayTimeline(DIA, FIN, {
    deliverables: [{ starts_at: h(18), task_id: 3, title: "Informe", overdue: false }],
    tasks: [{ id: 3, enabled: true, next_run: h(9), title: "Informe" }],
  });
  assert.strictEqual(r.total, 1, "el aviso sobra si la entrega ya está en el día");
  assert.strictEqual(r.items[0].kind, "due", "manda el plazo, no el aviso");
}

// ── Vencidas ─────────────────────────────────────────────────────────────────

{
  const r = dayTimeline(DIA, FIN, {
    deliverables: [
      { starts_at: h(9), task_id: 1, title: "Tarde", overdue: true },
      { starts_at: h(10), task_id: 2, title: "A tiempo", overdue: false },
    ],
  });
  assert.strictEqual(r.overdue, 1, "hay que poder marcar el día distinto");
}

// ── Bordes del día ───────────────────────────────────────────────────────────

{
  // Un evento de 23:00 a 01:00 pertenece a los dos días: se filtra por
  // solapamiento, no por hora de inicio.
  const anoche = { starts_at: DIA - 3600, ends_at: DIA + 3600, title: "Guardia", all_day: false };
  assert.strictEqual(dayTimeline(DIA, FIN, { events: [anoche] }).total, 1);

  // Una entrega, en cambio, es un instante: o cae dentro o no.
  assert.strictEqual(
    dayTimeline(DIA, FIN, { deliverables: [{ starts_at: DIA - 1, task_id: 1, title: "Ayer" }] }).total, 0,
  );
  assert.strictEqual(
    dayTimeline(DIA, FIN, { deliverables: [{ starts_at: FIN, task_id: 1, title: "Mañana" }] }).total, 0,
    "el final del rango no pertenece al día, o cada entrega saldría dos veces",
  );
}

{
  // Lo de todo el día va primero, pase lo que pase con su hora nominal.
  const r = dayTimeline(DIA, FIN, {
    events: [
      { starts_at: h(9), ends_at: h(10), title: "Reunión", all_day: false },
      { starts_at: h(23), ends_at: FIN, title: "Festivo", all_day: true },
    ],
  });
  assert.strictEqual(r.items[0].title, "Festivo");
}

{
  // Una tarea pausada no ocupa espacio en el día.
  const r = dayTimeline(DIA, FIN, { tasks: [{ id: 1, enabled: false, next_run: h(9), title: "Pausada" }] });
  assert.strictEqual(r.total, 0);
}

// Y un día vacío no revienta.
assert.strictEqual(dayTimeline(DIA, FIN, {}).total, 0);
assert.strictEqual(dayTimeline(DIA, FIN).total, 0);

// ── La cuenta atrás de una tarea ────────────────────────────────────────────

{
  const dentroDe = segundos => formatTaskTime(Date.now() / 1000 + segundos);

  // 7200 s son dos horas. Se leían como «1h 60m»: las horas y los minutos se
  // redondeaban por separado, y el `ceil` de los minutos podía llegar a 60 sin
  // llevarse la hora.
  assert.strictEqual(dentroDe(7200), "2h");
  assert.strictEqual(dentroDe(3600), "1h");
  assert.strictEqual(dentroDe(3600 * 3 - 1), "3h");
  assert.strictEqual(dentroDe(5400), "1h 30m");

  // Nunca puede salir un número de minutos que no existe en un reloj.
  for (let s = 3600; s < 86400; s += 137) {
    const texto = dentroDe(s);
    const m = /(\d+)h (\d+)m/.exec(texto);
    if (m) assert.ok(Number(m[2]) < 60, `minutos imposibles en ${texto} (${s}s)`);
  }

  // Y los extremos siguen siendo lo que eran.
  assert.strictEqual(formatTaskTime(0), "Pendiente");
  assert.strictEqual(dentroDe(-600), "VENCIDA");
  assert.strictEqual(dentroDe(-5), "AHORA");
}

console.log("day.test.js OK");
