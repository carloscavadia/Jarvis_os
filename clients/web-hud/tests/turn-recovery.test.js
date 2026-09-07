// Test del cierre de turno del HUD.
//
// Ejecuta:  node clients/web-hud/tests/turn-recovery.test.js
//
// El caso real: tras aprobar «enciende la luz del game room», la respuesta se
// escribió en el chat pero el HUD quedó inservible —«JARVIS está respondiendo…»
// permanente, ESTADO clavado en HABLANDO y la palabra de activación muerta—.
//
// La causa: todo el desbloqueo colgaba de que llegara un mensaje concreto, el
// frame final `reply`. El texto ya estaba en pantalla porque había llegado por
// streaming, así que parecía que el turno había terminado; pero sin ese frame
// `finishStreamReply` no corría, y con él se perdían el `setBusy(false)`, el
// regreso a reposo y la reapertura del micrófono.
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
  return js.slice(match.index, index + 1);
}

function extractConst(name) {
  const match = new RegExp(`\\n  const ${name} = [^;]*;`).exec(js);
  if (!match) throw new Error(`No encuentro la constante ${name}`);
  return match[0];
}

// Un HUD mínimo: solo lo que estas funciones tocan de verdad.
function montar({ vozRota = false } = {}) {
  const H = {};
  const registro = [];
  const stub = (name) => `function ${name}(){}`;
  const cuerpo = [
    // Temporizadores de mentira: el vigilante arma uno de 45 s por turno, y en
    // Node eso mantiene vivo el proceso hasta que vence. Aquí el vencimiento se
    // provoca llamando a `giveUpOnTurn`, así que no hace falta esperarlo.
    "let idTemporizador = 0;",
    "function setTimeout(){ return ++idTemporizador; }",
    "function clearTimeout(){}",
    "let busy=false, pendingApproval=null, pendingProactive=null, activeReply=null;",
    "let activeReplyText='', replyTextComplete=false, streamDirectedToBoard=false;",
    "let currentTurnPresented=false, automaticTurn=false, requestStartedAt=0;",
    "let firstDeltaSeen=false, streamTokenIndex=0;",
    // El cierre de turno devuelve el ambiente a la calma.
    "let systemLoadTarget = 0;",
    "const logEl={scrollTop:0,scrollHeight:0};",
    "const document={createElement:()=>({style:{},classList:{add(){},remove(){}},appendChild(){},set textContent(v){},get textContent(){return '';}})};",
    "let voiceSocket=null, currentToolCard=null, ws={readyState:1,send(){}};",
    "const messageInput={disabled:false,placeholder:'',value:'',focus(){}};",
    "const approvalActions={hidden:true}, approvalApprove={}, approvalDeny={};",
    "const approvalApproveSession={};",
    "const rLatency={textContent:''};",
    "function addLine(quien, texto){ H.registro.push([quien, texto]);"
    + " return {line:{classList:{remove(){}}}, text:{appendChild(){}, textContent:''}}; }",
    [
      "stopWakeListening", "stopConversationFollowUp", "scheduleWakeListening",
      "showToolEvent", "receiveMusicEvent", "firePulse", "setState", "setEmotion",
      "showGoalProgress", "renderStructuredText", "showOnBoard", "updateBoardContent",
      "beginStreamSpeech", "fetchTasks", "presentationFormat",
      "cancelSpeechQueue", "dropQueuedSpeech",
    ].map(stub).join("\n"),
    vozRota
      ? "function finishStreamSpeech(){ throw new ReferenceError('prepareSpeechText is not defined'); }"
        + "\nfunction appendStreamSpeech(){ throw new ReferenceError('prepareSpeechText is not defined'); }"
      : "function finishStreamSpeech(){}\nfunction appendStreamSpeech(){}",
    extractConst("BUSY_WATCHDOG_MS"),
    "let busyWatchdog = null;",
    extractFunction("armBusyWatchdog"),
    extractFunction("giveUpOnTurn"),
    extractFunction("setBusy"),
    extractFunction("boardReplySummary"),
    extractFunction("finishStreamReply"),
    extractFunction("startStreamReply"),
    extractFunction("appendStreamDelta"),
    extractFunction("handleGatewayMessage"),
    "H.setBusy=setBusy; H.recibir=handleGatewayMessage; H.rendirse=giveUpOnTurn;",
    "H.pendiente=(p)=>{pendingApproval=p;};",
    "H.estado=()=>({busy, bloqueado: messageInput.disabled, replyTextComplete});",
  ].join("\n");
  H.registro = registro;
  new Function("H", cuerpo)(H);
  return H;
}

function prueba(nombre, fn) {
  fn();
  console.log(`  ok  ${nombre}`);
}

prueba("el turno normal con aprobación termina desbloqueado", () => {
  const H = montar();
  H.setBusy(true);
  H.recibir({ type: "approval_required", approval_id: "a1", tool: "run_connector_module_action", arguments: {}, summary: "Encender la luz" });
  assert.strictEqual(H.estado().bloqueado, false, "hay que poder decidir");
  H.pendiente({ approval_id: "a1", tool: "run_connector_module_action", arguments: {} });
  H.setBusy(true);
  H.recibir({ type: "approval_resolved", approval_id: "a1", approved: true });
  assert.strictEqual(H.estado().busy, true, "mientras trabaja, bloqueado");
  H.recibir({ type: "reply", reply: "He encendido la luz de tu game room." });
  assert.strictEqual(H.estado().bloqueado, false);
});

prueba("una resolución que llega tarde no vuelve a bloquear", () => {
  // El orden que congelaba el HUD: la respuesta primero, la resolución después.
  const H = montar();
  H.recibir({ type: "reply", reply: "He encendido la luz." });
  assert.strictEqual(H.estado().bloqueado, false);
  H.recibir({ type: "approval_resolved", approval_id: "a1", approved: true });
  assert.strictEqual(H.estado().bloqueado, false, "se volvió a bloquear sin turno que esperar");
});

prueba("la resolución de una aprobación ajena tampoco bloquea", () => {
  // La decidida por el canal de voz: ese canal no deja `pendingApproval` aquí.
  const H = montar();
  H.recibir({ type: "approval_resolved", approval_id: "de-voz", approved: true });
  assert.strictEqual(H.estado().bloqueado, false);
});

prueba("un turno sin frame final acaba desbloqueando el compositor", () => {
  const H = montar();
  H.setBusy(true);
  H.recibir({ type: "reply_start" });
  H.recibir({ type: "reply_delta", delta: "He encendido la luz de tu game room." });
  // Aquí el servidor calla: nunca llega `reply`. Antes se quedaba así siempre.
  assert.strictEqual(H.estado().bloqueado, true, "durante el turno, bloqueado");

  H.rendirse();

  const e = H.estado();
  assert.strictEqual(e.bloqueado, false, "el compositor sigue bloqueado");
  assert.strictEqual(e.busy, false);
  // Sin esta bandera la cola de voz gira sin volver nunca a reposo.
  assert.strictEqual(e.replyTextComplete, true, "la cola de voz se quedaría girando");
  assert.ok(
    H.registro.some(([, texto]) => /sin dar señales/.test(texto)),
    "hay que decirle al usuario por qué se desbloqueó",
  );
});

prueba("el vigilante no se dispara si el turno ya terminó bien", () => {
  const H = montar();
  H.setBusy(true);
  H.recibir({ type: "reply", reply: "Listo." });
  const antes = H.registro.length;
  H.rendirse();
  assert.strictEqual(H.registro.length, antes, "avisó de un fallo que no existía");
});

prueba("un fallo de la voz no impide cerrar el turno", () => {
  // La regresión exacta: `prepareSpeechText` no existía, y `finishStreamReply`
  // llama a la voz ANTES de su `setBusy(false)`. La excepción se llevaba por
  // delante el desbloqueo del teclado, el cierre y el regreso a reposo, con la
  // respuesta ya escrita en pantalla.
  const H = montar({ vozRota: true });
  const errorReal = console.error;
  console.error = () => {};   // el fallo se registra a propósito; aquí solo estorba
  H.setBusy(true);
  H.recibir({ type: "reply_start" });
  H.recibir({ type: "reply_delta", delta: "He encendido la luz." });
  H.recibir({ type: "reply", reply: "He encendido la luz del game room." });

  console.error = errorReal;

  const e = H.estado();
  assert.strictEqual(e.bloqueado, false, "la voz se llevó por delante el desbloqueo");
  assert.strictEqual(e.replyTextComplete, true, "el turno quedó sin cerrar");
});

prueba("la cola de voz tiene tope de espera, no gira indefinidamente", () => {
  // La guarda vive dentro de runSpeechQueue; se comprueba sobre el fuente
  // porque el bucle es asíncrono y depende de temporizadores reales.
  assert.ok(/const MAX_WAITING_ROUNDS = \d+;/.test(js), "falta el tope de rondas");
  assert.ok(
    /if \(!replyTextComplete && waitingRounds < MAX_WAITING_ROUNDS\)/.test(js),
    "el bucle sigue esperando sin límite a que el turno se cierre",
  );
});

console.log("\nCierre de turno correcto");
