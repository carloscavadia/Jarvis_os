// Test del pizarrón cuando lo que llega es una lista.
//
// Ejecuta:  node clients/web-hud/tests/dataset.test.js
//
// El caso real: «lista mis dispositivos de la casa y de Home Assistant». La
// herramienta devolvía 148 entidades, el gateway las cortaba por caracteres a
// mitad de un objeto, el HUD no podía parsear el JSON y caía a texto plano: una
// pared de llaves en pantalla. Aquí se fija lo que debe pasar en su lugar.
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

function extractConst(name) {
  const match = new RegExp(`\\n  const ${name} = [\\s\\S]*?;\\n`).exec(js);
  if (!match) throw new Error(`No encuentro la constante ${name}`);
  return match[0] + `\nharness.${name} = ${name};`;
}

const harness = {};
new Function("harness", [
  extractConst("DATASET_PREFERRED_COLUMNS"),
  extractConst("DATASET_PREFERRED_GROUPS"),
  extractConst("DATASET_MAX_COLUMNS"),
  extractConst("DATASET_MIN_RECORDS"),
  extractFunction("isRecordArray"),
  extractFunction("analyzeDataset"),
  extractFunction("datasetCell"),
].join("\n"))(harness);

const { analyzeDataset, datasetCell } = harness;

// Los datos exactos de la captura del usuario.
const HOME_ASSISTANT = {
  total: 148, returned: 148, offset: 0, has_more: false,
  entities: [
    { entity_id: "update.home_assistant_supervisor_update", name: "Home Assistant Supervisor Update", domain: "update", state: "off" },
    { entity_id: "update.home_assistant_core_update", name: "Home Assistant Core Update", domain: "update", state: "on" },
    { entity_id: "update.matter_server_update", name: "Matter Server Update", domain: "update", state: "on" },
    { entity_id: "conversation.home_assistant", name: "Home Assistant", domain: "conversation", state: "unknown" },
    { entity_id: "event.backup_automatic_backup", name: "Backup Automatic backup", domain: "event", state: "unknown" },
    { entity_id: "sensor.backup_backup_manager_state", name: "Backup Backup Manager state", domain: "sensor", state: "idle", device_class: "enum" },
    { entity_id: "sensor.backup_next_scheduled_automatic_backup", name: "Backup Next scheduled automatic backup", domain: "sensor", state: "unknown", device_class: "timestamp" },
    { entity_id: "zone.home", name: "Casa", domain: "zone", state: "0" },
  ],
};

function prueba(nombre, fn) {
  fn();
  console.log(`  ok  ${nombre}`);
}

prueba("reconoce la lista dentro del sobre {total, entities}", () => {
  const analisis = analyzeDataset(HOME_ASSISTANT);
  assert.ok(analisis, "no detectó ninguna lista");
  assert.strictEqual(analisis.source, "entities");
  assert.strictEqual(analisis.total, 8);
});

prueba("agrupa por dominio, que es lo que parte la lista en montones útiles", () => {
  assert.strictEqual(analyzeDataset(HOME_ASSISTANT).groupBy, "domain");
});

prueba("pone el nombre legible antes que el identificador técnico", () => {
  const { columns } = analyzeDataset(HOME_ASSISTANT);
  assert.strictEqual(columns[0], "name");
  assert.ok(columns.indexOf("entity_id") < columns.indexOf("state"));
});

prueba("no agrupa por un campo con un valor distinto por fila", () => {
  // entity_id es único: agrupar por él daría 8 grupos de uno, peor que nada.
  assert.notStrictEqual(analyzeDataset(HOME_ASSISTANT).groupBy, "entity_id");
});

prueba("acepta una lista suelta, sin sobre", () => {
  const analisis = analyzeDataset(HOME_ASSISTANT.entities);
  assert.ok(analisis);
  assert.strictEqual(analisis.source, "");
  assert.strictEqual(analisis.groupBy, "domain");
});

prueba("elige el array de registros más largo cuando hay varios", () => {
  const analisis = analyzeDataset({
    warnings: [{ code: 1 }, { code: 2 }],
    entities: HOME_ASSISTANT.entities,
  });
  assert.strictEqual(analisis.source, "entities");
});

prueba("no fuerza la vista de lista sobre datos que no lo son", () => {
  assert.strictEqual(analyzeDataset({ cpu: 12.4, ram: 34.8 }), null);
  assert.strictEqual(analyzeDataset([1, 2, 3]), null);
  assert.strictEqual(analyzeDataset("texto suelto"), null);
  assert.strictEqual(analyzeDataset([{ a: 1 }]), null, "un solo registro no es una lista");
  assert.strictEqual(analyzeDataset({ nested: [{ a: [1] }, { a: [2] }] }), null,
    "sin columnas escalares no hay tabla que pintar");
});

prueba("funciona con datos que no son de Home Assistant", () => {
  // La forma la deciden los datos: nada del renderizador sabe de domótica.
  const tareas = [
    { id: 1, title: "Regar plantas", status: "pending" },
    { id: 2, title: "Backup", status: "done" },
    { id: 3, title: "Actualizar", status: "pending" },
  ];
  const analisis = analyzeDataset(tareas);
  assert.strictEqual(analisis.groupBy, "status");
  assert.strictEqual(analisis.columns[0], "title");
});

prueba("los huecos se muestran como raya, no como undefined", () => {
  assert.strictEqual(datasetCell(undefined), "—");
  assert.strictEqual(datasetCell(null), "—");
  assert.strictEqual(datasetCell(""), "—");
  assert.strictEqual(datasetCell(0), "0");
  assert.strictEqual(datasetCell(false), "no");
});

console.log("\nPizarrón dinámico correcto");
