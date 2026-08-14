// Configuración local del HUD.
//
//   cp config.example.js config.js     y rellena los valores.
//
// El HUD es un fichero que abre el navegador, así que **no puede leer el .env
// del servidor**. Este fichero es su equivalente: se edita una vez en el equipo
// donde sirves el HUD, no se sube al repositorio (está en .gitignore) y evita
// tener que escribir la clave y pulsar Conectar cada vez.
//
// Si no existe, el HUD funciona igual: pide la clave y la recuerda en el
// navegador tras la primera conexión correcta.

window.JARVIS_CONFIG = {
  // Debe coincidir con JARVIS_GATEWAY_API_KEY del servidor.
  hubKey: "",

  // Dirección del gateway. Cambia la IP si tu servidor no es esta.
  wsUrl: "ws://192.168.68.100:8080/ws/hub-test",

  // Conectar solo al abrir, sin pulsar el botón.
  autoConnect: true,
};
