// Test de las ventanas flotantes del HUD.
//
// Ejecuta:  node clients/web-hud/tests/windows.test.js
//
// Lo que se fija aquí es sobre todo lo que puede dejar el HUD inservible sin
// que se note al probarlo un momento: una ventana arrastrada fuera de la
// pantalla no se puede recuperar —no queda barra de título que agarrar— y solo
// se arregla recargando. Y un vídeo que sigue sonando después de cerrar su
// ventana no tiene ningún sitio donde pararse.
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
  const match = new RegExp(`\\n  const ${name} = \\{[\\s\\S]*?\\n  \\};`).exec(js);
  if (!match) throw new Error(`No encuentro la constante ${name}`);
  return match[0] + `\nharness.${name} = ${name};`;
}

function extractConstList(name) {
  const match = new RegExp(`\\n  const ${name} = \\[[^\\]]*\\];`).exec(js);
  if (!match) throw new Error(`No encuentro la lista ${name}`);
  return match[0] + `\nharness.${name} = ${name};`;
}

function extractBinding(name) {
  const match = new RegExp(`\\n  (?:const|let) ${name} = [^\\n]*;`).exec(js);
  if (!match) throw new Error(`No encuentro la variable ${name}`);
  return match[0] + `\nharness.${name} = () => ${name};`;
}

// ── DOM mínimo ────────────────────────────────────────────────────────────
// Solo lo que el gestor de ventanas toca de verdad. Un DOM completo escondería
// justo lo que queremos ver: qué llama y con qué.
function makeElement(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    parent: null,
    style: {},
    attributes: {},
    listeners: {},
    textContent: "",
    className: "",
    box: { left: 0, top: 0, width: 640, height: 460 },
    classList: {
      add(...names) { for (const n of names) if (!el.className.split(" ").includes(n)) el.className = `${el.className} ${n}`.trim(); },
      remove(...names) { el.className = el.className.split(" ").filter(n => n && !names.includes(n)).join(" "); },
      contains(name) { return el.className.split(" ").includes(name); },
      toggle(name, force) {
        const has = el.classList.contains(name);
        const want = force === undefined ? !has : Boolean(force);
        if (want) el.classList.add(name); else el.classList.remove(name);
        return want;
      },
    },
    setAttribute(key, value) { el.attributes[key] = String(value); },
    getAttribute(key) { return el.attributes[key]; },
    append(...nodes) { for (const n of nodes) { if (n && n.tagName) { n.parent = el; el.children.push(n); } } },
    appendChild(node) { el.append(node); return node; },
    replaceChildren(...nodes) { el.children = []; el.append(...nodes); },
    remove() { if (el.parent) el.parent.children = el.parent.children.filter(c => c !== el); el.parent = null; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    dispatch(type, event = {}) {
      const base = { preventDefault() {}, target: el };
      for (const fn of el.listeners[type] || []) fn({ ...base, ...event });
    },
    closest(selector) {
      // Solo lo que usa el código: coincidencia por nombre de etiqueta.
      const etiquetas = selector.split(",").map(s => s.trim().toUpperCase());
      let nodo = el;
      while (nodo) { if (etiquetas.includes(nodo.tagName)) return nodo; nodo = nodo.parent; }
      return null;
    },
    getBoundingClientRect() {
      return {
        left: parseFloat(el.style.left) || el.box.left,
        top: parseFloat(el.style.top) || el.box.top,
        width: parseFloat(el.style.width) || el.box.width,
        height: parseFloat(el.style.height) || el.box.height,
      };
    },
    querySelectorAll(selector) {
      const wanted = selector.split(",").map(s => s.trim().toUpperCase());
      const found = [];
      const walk = node => { for (const child of node.children) { if (wanted.includes(child.tagName)) found.push(child); walk(child); } };
      walk(el);
      return found;
    },
    setPointerCapture() {}, releasePointerCapture() {},
  };
  return el;
}

const body = makeElement("body");
global.document = { createElement: makeElement, body };
global.window = { innerWidth: 1440, innerHeight: 900 };

const harness = {};
new Function("harness", [
  extractBinding("openWindows"),
  extractBinding("windowZ"),
  extractBinding("windowsOpened"),
  extractFunction("clampWindowBox"),
  extractFunction("nextWindowPosition"),
  extractFunction("focusWindow"),
  extractFunction("closeWindow"),
  extractFunction("closeTopWindow"),
  extractFunction("attachWindowDrag"),
  extractFunction("openWindow"),
  extractFunction("youtubeVideoId"),
  extractFunction("youtubeEmbedUrl"),
  extractConst("YOUTUBE_PLAYER_ERRORS"),
  extractFunction("youtubePlayerError"),
  extractBinding("BROWSER_VIEWPORT"),
  extractFunction("browserPagePoint"),
].join("\n"))(harness);

const { clampWindowBox, nextWindowPosition, openWindow, closeWindow, closeTopWindow, focusWindow, youtubeEmbedUrl, browserPagePoint } = harness;
const openWindowsMap = harness.openWindows();
const VIEWPORT = { width: 1440, height: 900 };

// ── Que la ventana no se pueda perder ─────────────────────────────────────

{
  // Arrastrada muy a la derecha, sigue quedando un trozo agarrable.
  const box = clampWindowBox({ left: 5000, top: 40, width: 640, height: 460 }, VIEWPORT);
  assert.ok(box.left < VIEWPORT.width, "la ventana no puede salir por la derecha");
  assert.ok(box.left + box.width > 0, "tiene que quedar algo visible");
}

{
  // Arrastrada arriba del todo: la barra de título nunca sube por encima del borde.
  const box = clampWindowBox({ left: 200, top: -800, width: 640, height: 460 }, VIEWPORT);
  assert.strictEqual(box.top, 0, "la barra de título tiene que seguir siendo agarrable");
}

{
  // Y por abajo tampoco desaparece del todo.
  const box = clampWindowBox({ left: 200, top: 9000, width: 640, height: 460 }, VIEWPORT);
  assert.ok(box.top < VIEWPORT.height, "la barra de título no puede caer fuera de la pantalla");
}

{
  // Muy a la izquierda: se permite que se salga, pero no entera.
  const box = clampWindowBox({ left: -5000, top: 40, width: 640, height: 460 }, VIEWPORT);
  assert.ok(box.left + box.width > 0, "no puede desaparecer por la izquierda");
}

{
  // Una ventana más grande que la pantalla se recorta a la pantalla.
  const box = clampWindowBox({ left: 0, top: 0, width: 4000, height: 4000 }, { width: 800, height: 600 });
  assert.strictEqual(box.width, 800);
  assert.strictEqual(box.height, 600);
}

{
  // Y una diminuta conserva un tamaño usable.
  const box = clampWindowBox({ left: 0, top: 0, width: 10, height: 10 }, VIEWPORT);
  assert.ok(box.width >= 260 && box.height >= 170, "una ventana de 10px no se puede ni cerrar");
}

{
  // En una pantalla pequeña la cascada tampoco deja nada fuera.
  const pequena = { width: 420, height: 640 };
  for (let i = 0; i < 12; i += 1) {
    const box = nextWindowPosition(i, pequena, { width: 640, height: 460 });
    assert.ok(box.top >= 0 && box.top < pequena.height, `ventana ${i} fuera por arriba/abajo`);
    assert.ok(box.left + box.width > 0 && box.left < pequena.width, `ventana ${i} fuera por los lados`);
  }
}

{
  // Dos ventanas seguidas no se superponen exactamente: si lo hicieran, la
  // segunda taparía a la primera sin que se notase que hay dos.
  const a = nextWindowPosition(0, VIEWPORT, { width: 640, height: 460 });
  const b = nextWindowPosition(1, VIEWPORT, { width: 640, height: 460 });
  assert.notDeepStrictEqual({ left: a.left, top: a.top }, { left: b.left, top: b.top });
}

{
  // Y siguen cascadeando aunque tengan tamaños distintos. Este es el caso que
  // falló de verdad en el navegador: la posición se calculaba centrando cada
  // ventana por su propio ancho, así que una foto de 720 y un PDF de 780
  // acababan a dos píxeles el uno del otro y el PDF tapaba la foto entera.
  const foto = nextWindowPosition(0, VIEWPORT, { width: 720, height: 540 });
  const pdf = nextWindowPosition(1, VIEWPORT, { width: 780, height: 640 });
  const separacion = Math.abs(pdf.left - foto.left) + Math.abs(pdf.top - foto.top);
  assert.ok(separacion >= 40, `dos ventanas de distinto tamaño se tapan (separación ${separacion}px)`);
}

// ── Abrir, reutilizar, cerrar ─────────────────────────────────────────────

{
  const uno = openWindow({ id: "viewer:image:foto.png", title: "La foto", kind: "IMAGEN" });
  assert.strictEqual(openWindowsMap.size, 1);
  assert.ok(body.children.includes(uno.frame), "la ventana tiene que estar en el documento");

  // Pedir lo mismo otra vez no apila copias: refresca la que ya está.
  const otra = openWindow({ id: "viewer:image:foto.png", title: "La foto de nuevo", kind: "IMAGEN" });
  assert.strictEqual(otra, uno, "la misma id tiene que reutilizar la ventana");
  assert.strictEqual(openWindowsMap.size, 1, "no se puede duplicar la ventana");
  assert.strictEqual(otra.body.children.length, 0, "al reutilizar, el cuerpo se vacía");

  // Otra cosa distinta sí abre su propia ventana: conviven.
  const pdf = openWindow({ id: "viewer:pdf:informe.pdf", title: "Informe", kind: "PDF" });
  assert.strictEqual(openWindowsMap.size, 2, "dos cosas distintas son dos ventanas");
  assert.ok(pdf.frame.classList.contains("focused"), "la última abierta manda");
  assert.ok(!uno.frame.classList.contains("focused"));

  // Volver a la primera la trae al frente.
  focusWindow("viewer:image:foto.png");
  assert.ok(Number(uno.frame.style.zIndex) > Number(pdf.frame.style.zIndex));

  // Escape cierra la de encima, no todas.
  assert.strictEqual(closeTopWindow(), true);
  assert.strictEqual(openWindowsMap.size, 1, "Escape cierra una, no el escritorio entero");
  assert.ok(openWindowsMap.has("viewer:pdf:informe.pdf"), "la que se cierra es la de encima");

  closeWindow("viewer:pdf:informe.pdf");
  assert.strictEqual(openWindowsMap.size, 0);
  assert.strictEqual(body.children.length, 0, "cerrar tiene que sacarla del documento");
  assert.strictEqual(closeTopWindow(), false, "sin ventanas, Escape no hace nada");
}

{
  // Cerrar un vídeo lo tiene que callar. Sin esto el iframe sigue vivo un
  // instante y el sonido continúa sin ventana desde donde pararlo.
  const win = openWindow({ id: "viewer:video:x", title: "Vídeo", kind: "VÍDEO" });
  const iframe = document.createElement("iframe");
  iframe.src = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ";
  win.body.appendChild(iframe);
  closeWindow("viewer:video:x");
  assert.strictEqual(iframe.src, "about:blank", "el iframe se vacía al cerrar");
}

// ── Arrastre ──────────────────────────────────────────────────────────────

{
  const win = openWindow({ id: "drag", title: "Arrastrable", kind: "" });
  const head = win.frame.children[0];
  win.frame.style.left = "300px"; win.frame.style.top = "200px";
  win.frame.style.width = "640px"; win.frame.style.height = "460px";

  head.dispatch("pointerdown", { button: 0, clientX: 400, clientY: 220, pointerId: 1 });
  assert.ok(win.frame.classList.contains("dragging"), "arrastrando se marca la ventana");
  head.dispatch("pointermove", { clientX: 500, clientY: 320, pointerId: 1 });
  assert.strictEqual(win.frame.style.left, "400px");
  assert.strictEqual(win.frame.style.top, "300px");
  head.dispatch("pointerup", { pointerId: 1 });
  assert.ok(!win.frame.classList.contains("dragging"));

  // Soltado el puntero, moverlo ya no arrastra nada.
  head.dispatch("pointermove", { clientX: 900, clientY: 700, pointerId: 1 });
  assert.strictEqual(win.frame.style.left, "400px", "sin botón pulsado no se mueve");

  // Un arrastre hasta muy lejos queda recortado, no perdido.
  head.dispatch("pointerdown", { button: 0, clientX: 400, clientY: 320, pointerId: 2 });
  head.dispatch("pointermove", { clientX: 9000, clientY: 9000, pointerId: 2 });
  const left = parseFloat(win.frame.style.left);
  const top = parseFloat(win.frame.style.top);
  assert.ok(left < VIEWPORT.width && top < VIEWPORT.height, "no se puede arrastrar fuera de la pantalla");
  head.dispatch("pointerup", { pointerId: 2 });

  // Un pointerdown sobre un botón de la barra NO arranca un arrastre.
  //
  // Este es el fallo que se me escapó: los tests cerraban las ventanas llamando
  // a closeWindow(), nunca pulsando la ×. Al capturar el puntero en la barra,
  // el pointerup dejaba de caer sobre el botón y el clic no llegaba a
  // dispararse: las ventanas no se podían cerrar con el ratón.
  const cerrar = head.children.find(c => c.textContent === "×");
  assert.ok(cerrar, "la barra de título tiene que tener botón de cerrar");
  const antesLeft = win.frame.style.left;
  head.dispatch("pointerdown", { button: 0, clientX: 700, clientY: 110, pointerId: 9, target: cerrar });
  assert.ok(!win.frame.classList.contains("dragging"), "pulsar la × no puede iniciar un arrastre");
  head.dispatch("pointermove", { clientX: 900, clientY: 400, pointerId: 9 });
  assert.strictEqual(win.frame.style.left, antesLeft, "la ventana no se mueve al pulsar un botón");
  head.dispatch("pointerup", { pointerId: 9 });

  // Y el botón sigue cerrando de verdad.
  cerrar.onclick();
  assert.ok(!openWindowsMap.has("drag"), "la × tiene que cerrar la ventana");
  openWindow({ id: "drag", title: "Arrastrable", kind: "" });

  // La esquina redimensiona en vez de mover.
  const grip = win.frame.children[2];
  win.frame.style.left = "100px"; win.frame.style.top = "100px";
  win.frame.style.width = "640px"; win.frame.style.height = "460px";
  grip.dispatch("pointerdown", { button: 0, clientX: 740, clientY: 560, pointerId: 3 });
  grip.dispatch("pointermove", { clientX: 840, clientY: 610, pointerId: 3 });
  assert.strictEqual(win.frame.style.left, "100px", "redimensionar no mueve la ventana");
  assert.strictEqual(win.frame.style.width, "740px");
  assert.strictEqual(win.frame.style.height, "510px");
  grip.dispatch("pointerup", { pointerId: 3 });

  // Y no se puede encoger hasta dejarla inservible.
  grip.dispatch("pointerdown", { button: 0, clientX: 840, clientY: 610, pointerId: 4 });
  grip.dispatch("pointermove", { clientX: 0, clientY: 0, pointerId: 4 });
  assert.ok(parseFloat(win.frame.style.width) >= 260);
  assert.ok(parseFloat(win.frame.style.height) >= 170);
  grip.dispatch("pointerup", { pointerId: 4 });

  closeWindow("drag");
}

// ── YouTube: la URL del iframe la componemos nosotros ─────────────────────

{
  const esperado = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&enablejsapi=1";
  for (const entrada of [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
  ]) {
    assert.strictEqual(youtubeEmbedUrl(entrada), esperado, `mal embebido: ${entrada}`);
  }

  // El origen viaja al reproductor. Sin él —y sin cabecera Referer— YouTube
  // responde «Error 153» y no arranca: es lo que pasaba por tener el iframe con
  // referrerPolicy="no-referrer".
  const conOrigen = youtubeEmbedUrl("dQw4w9WgXcQ", "https://jarvis.example");
  assert.ok(conOrigen.includes("origin=https%3A%2F%2Fjarvis.example"), conOrigen);
  assert.ok(conOrigen.includes("enablejsapi=1"), "sin enablejsapi el reproductor no puede avisar de errores");

  // Un origen opaco (`null`) no se manda: rompería la comprobación en vez de
  // ayudarla.
  assert.ok(!youtubeEmbedUrl("dQw4w9WgXcQ", "null").includes("origin="));
}

// Y el iframe NO puede llevar referrerPolicy="no-referrer": esa era la causa
// del Error 153. Se comprueba sobre el código real, porque es una línea que se
// vuelve a colar con facilidad «por privacidad» sin ver el efecto.
{
  const bloque = js.slice(js.indexOf('if (kind === "video")'), js.indexOf('if (kind === "web")'));
  assert.ok(bloque.length > 0, "no encuentro el visor de vídeo");
  // Sin los comentarios: uno de ellos explica precisamente por qué no debe
  // estar, y si no se quitan el test se encuentra a sí mismo.
  const codigo = bloque.replace(/\/\/[^\n]*/g, "");
  assert.ok(
    !/referrerPolicy\s*=\s*["']no-referrer["']/.test(codigo),
    "el iframe de YouTube no puede ocultar el origen: el reproductor lo rechaza",
  );
}

// Los errores del reproductor se traducen a algo que se entiende.
{
  assert.match(harness.youtubePlayerError(150), /no permite verlo fuera de YouTube/);
  assert.match(harness.youtubePlayerError(101), /no permite verlo fuera de YouTube/);
  assert.match(harness.youtubePlayerError(153), /rechazado/);
  assert.match(harness.youtubePlayerError(100), /ya no existe|privado/);
  // Un código que no conocemos no deja al usuario sin explicación.
  assert.ok(harness.youtubePlayerError(999).length > 10);
}

// Un dominio que solo empieza por youtube.com no es YouTube. Es el caso que de
// verdad importa: engaña a cualquier comprobación con startswith, y lo que
// acabaría dentro del iframe sería la página de quien registre ese dominio.
for (const malo of [
  "https://youtube.com.atacante.net/watch?v=dQw4w9WgXcQ",
  "https://ejemplo.com/watch?v=dQw4w9WgXcQ",
  "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "javascript:alert(1)",
  "",
]) {
  assert.strictEqual(youtubeEmbedUrl(malo), "", `no debería embeber: ${malo}`);
}

// ── Navegador: el clic tiene que caer donde el usuario cree ───────────────
//
// El navegador corre en el servidor a 1280×800, pero la captura se escala al
// ancho de la ventana. Sin traducir las coordenadas, el clic aterriza en otro
// sitio y parece que el navegador va por libre.

{
  // Ventana a media escala: pinchar en el centro de la imagen es el centro real.
  const media = { width: 640, height: 400 };
  assert.deepStrictEqual(browserPagePoint(320, 200, media), { x: 640, y: 400 });
  assert.deepStrictEqual(browserPagePoint(0, 0, media), { x: 0, y: 0 });

  // A escala 1:1 las coordenadas pasan tal cual.
  const igual = { width: 1280, height: 800 };
  assert.deepStrictEqual(browserPagePoint(100, 250, igual), { x: 100, y: 250 });

  // Un clic en el borde, o fuera por un píxel de redondeo, se queda dentro de
  // la pestaña: una coordenada de 1280 en una ventana de 1280 de ancho está
  // fuera del área válida y Playwright la rechazaría.
  const borde = browserPagePoint(640, 400, media);
  assert.ok(borde.x < 1280 && borde.y < 800);
  const fuera = browserPagePoint(-50, 99999, media);
  assert.deepStrictEqual(fuera, { x: 0, y: 799 });

  // Y si aún no se conoce el tamaño mostrado, no se rompe.
  assert.deepStrictEqual(browserPagePoint(10, 10, null), { x: 10, y: 10 });
}

// ── El navegador vuelve a mirar solo ──────────────────────────────────────
//
// El servidor ya espera a que la página se asiente antes de capturar, pero un
// sitio pesado sigue pintando después. Sin estos vistazos, lo que se quedó a
// medias se queda a medias en pantalla para siempre: es lo que se veía como
// «el sitio se queda loading».

{
  const programadas = [];
  const realSetTimeout = global.setTimeout;
  const realClearTimeout = global.clearTimeout;
  global.setTimeout = (fn, ms) => { programadas.push({ fn, ms }); return programadas.length; };
  global.clearTimeout = () => {};

  const h2 = {};
  new Function("harness", [
    extractBinding("browserRefreshTimers"),
    extractConstList("BROWSER_REFRESH_DELAYS"),
    extractFunction("programarRefrescoDelNavegador"),
  ].join("\n"))(h2);
  // `openWindows` y `browserAct` los resuelve el cierre en tiempo de ejecución;
  // aquí solo se comprueba el calendario, que es lo que decide si la ventana se
  // desatasca o no.
  global.openWindows = new Map([["browser", {}]]);
  let lecturas = 0;
  global.browserAct = orden => { lecturas += 1; assert.strictEqual(orden.action, "read"); };

  h2.programarRefrescoDelNavegador();
  assert.deepStrictEqual(programadas.map(p => p.ms), [1500, 4000], "hacen falta dos vistazos, no uno");

  for (const p of programadas) p.fn();
  assert.strictEqual(lecturas, 2, "los dos refrescos tienen que pedir una captura nueva");

  // Y un refresco NO puede volver a programarse a sí mismo: sería recapturar la
  // página para siempre.
  const antes = programadas.length;
  global.browserAct({ action: "read" });
  assert.strictEqual(programadas.length, antes, "un 'read' no puede encadenar más refrescos");

  global.setTimeout = realSetTimeout;
  global.clearTimeout = realClearTimeout;
}

console.log("windows.test.js OK");
