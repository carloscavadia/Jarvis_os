// Test de regresión del panel de conectores del HUD.
//
// Ejecuta:  node clients/web-hud/tests/connectors.test.js
//
// Regla que fija: cada cosa se prueba contra su propio endpoint. Un servidor MCP
// no vive en el registro de conectores, así que probarlo por ahí devolvía
// «Módulo no encontrado» con el servidor perfectamente guardado. El mensaje
// acusaba al guardado, que había funcionado, y mandaba a revisar la URL, el
// token y la integración de Home Assistant sin motivo.
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

// Colaboradores mínimos: sólo interesa a qué ruta se llama y qué se muestra.
function montar(respuesta) {
  const llamadas = [];
  const estados = [];
  const harness = {};
  new Function(
    "harness", "connectorRequest", "connectorApiHeaders", "setConnectorStatus",
    extractFunction("testConnector"),
  )(
    harness,
    async (ruta, opciones) => {
      llamadas.push({ ruta, metodo: opciones?.method });
      if (respuesta instanceof Error) throw respuesta;
      return respuesta;
    },
    () => ({ "X-Jarvis-Key": "k" }),
    (texto, clase) => estados.push({ texto, clase }),
  );
  return { probar: harness.testConnector, llamadas, estados };
}

(async () => {
  // Un servidor MCP se prueba contra la API de MCP.
  {
    const { probar, llamadas, estados } = montar({
      name: "homeassistant", tools_count: 12, tools: ["luz_on", "luz_off"],
    });
    const ok = await probar("homeassistant", "mcp");
    assert.strictEqual(ok, true);
    assert.strictEqual(llamadas[0].ruta, "/mcp/servers/homeassistant/test");
    assert.strictEqual(llamadas[0].metodo, "POST");
    // Y el resultado dice algo útil: cuántas herramientas ganó JARVIS.
    assert.match(estados.at(-1).texto, /12 herramientas/);
    assert.strictEqual(estados.at(-1).clase, "success");
  }

  // Un conector normal sigue yendo al registro de conectores.
  {
    const { probar, llamadas } = montar({ name: "correo", message: "OK" });
    await probar("correo", "n8n");
    assert.strictEqual(llamadas[0].ruta, "/connector-modules/test");
  }

  // Sin tipo —el caso de los botones del listado— tampoco cambia el destino.
  {
    const { probar, llamadas } = montar({ name: "correo", message: "OK" });
    await probar("correo");
    assert.strictEqual(llamadas[0].ruta, "/connector-modules/test");
  }

  // Un fallo real se sigue reportando: el arreglo no puede tapar errores.
  {
    const { probar, estados } = montar(new Error("token inválido"));
    const ok = await probar("homeassistant", "mcp");
    assert.strictEqual(ok, false);
    assert.match(estados.at(-1).texto, /token inválido/);
    assert.strictEqual(estados.at(-1).clase, "error");
  }

  // Un servidor que conecta pero no expone nada no puede parecer un éxito mudo.
  {
    const { probar, estados } = montar({ name: "vacio", tools_count: 0, tools: [] });
    await probar("vacio", "mcp");
    assert.match(estados.at(-1).texto, /ninguna/);
  }

  console.log("connectors.test.js OK");
})();
