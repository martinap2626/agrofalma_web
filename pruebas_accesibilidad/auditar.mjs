// Auditoría de accesibilidad con axe-core (WCAG 2.1 A y AA + buenas prácticas) y Lighthouse sobre cada pantalla, con sesión iniciada.
// Ver el README de esta carpeta. Uso:  node auditar.mjs http://127.0.0.1:8765 resultados/accesibilidad.json
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import puppeteer from "puppeteer-core";
import lighthouse from "lighthouse";

const require = createRequire(import.meta.url);
const axePath = require.resolve("axe-core/axe.min.js");
const [base, salida] = process.argv.slice(2);
if (!base || !salida) {
  console.error("Uso: node auditar.mjs <url del sistema> <archivo de salida .json>");
  process.exit(2);
}

// credenciales y datos de ejemplo por variables de entorno (nunca escritas en el código)
const env = process.env;
const CHROME = env.CHROME || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PUERTO = Number(env.PUERTO_DEPURACION || 9333);
const ADMIN = { usuario: env.ADMIN_USUARIO, contrasena: env.ADMIN_CLAVE };
const TRABAJADOR = { usuario: env.TRABAJADOR_USUARIO, contrasena: env.TRABAJADOR_CLAVE };
const ID_VENTA = env.ID_VENTA || "1";         // id de una venta existente
const ID_PRODUCTO = env.ID_PRODUCTO || "1";   // id de un producto existente
for (const [nombre, valor] of Object.entries({ ADMIN_USUARIO: ADMIN.usuario, ADMIN_CLAVE: ADMIN.contrasena, TRABAJADOR_USUARIO: TRABAJADOR.usuario, TRABAJADOR_CLAVE: TRABAJADOR.contrasena })) {
  if (!valor) {
    console.error(`Falta la variable de entorno ${nombre}`);
    process.exit(2);
  }
}

const RUTAS_ADMIN = [
  "/dashboard", "/ventas", `/ventas/${ID_VENTA}`, "/ventas/nueva", "/ventas/clientes", "/ventas/clientes/nuevo", "/ventas/creditos",
  "/productos", "/productos/nuevo", "/inventario", `/inventario/${ID_PRODUCTO}`, "/analitica", "/analitica/prediccion",
  "/analitica/segmentacion", "/analitica/campana", "/analitica/simulador",
  `/analitica/simulador?id_producto=${ID_PRODUCTO}&escenario=mas&valor_mas=50`, // el simulador con un resultado y su gráfico
  "/reportes", "/eventos", "/eventos/nuevo",
  "/usuarios", "/usuarios/nuevo", "/auditoria", "/ayuda", "/cuenta/contrasena",
];
const RUTAS_TRABAJADOR = ["/ventas/mis-ventas", "/ventas/nueva", "/productos", "/ventas/clientes", "/ventas/creditos", "/ayuda"];

const navegador = await puppeteer.launch({ executablePath: CHROME, headless: true, args: [`--remote-debugging-port=${PUERTO}`, "--no-sandbox"] });

// la política de seguridad del sistema (CSP) prohíbe scripts en línea; el auditor necesita inyectar axe-core, así que la salta SOLO en su pestaña
async function nuevaPagina() {
  const pagina = await navegador.newPage();
  await pagina.setBypassCSP(true);
  return pagina;
}

async function iniciarSesion(credenciales) {
  const pagina = await nuevaPagina();
  await pagina.goto(`${base}/login`, { waitUntil: "networkidle0" });
  await pagina.type("#usuario", credenciales.usuario);
  await pagina.type("#contrasena", credenciales.contrasena);
  await Promise.all([pagina.waitForNavigation({ waitUntil: "networkidle0" }), pagina.click('button[type="submit"]')]);
  return pagina;
}

async function cerrarSesion(pagina) {
  await pagina.evaluate(() => document.querySelector('form[action="/logout"] button')?.click());
  await pagina.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {});
}

async function auditar(pagina, ruta, rol) {
  const url = `${base}${ruta}`;
  await pagina.goto(url, { waitUntil: "networkidle0" });
  await pagina.addScriptTag({ path: axePath });
  const axe = await pagina.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] } });
    return {
      violaciones: r.violations.map((v) => ({ id: v.id, impacto: v.impact, ayuda: v.help, nodos: v.nodes.length, ejemplo: v.nodes[0].html.slice(0, 160), objetivo: v.nodes[0].target.join(" ") })),
      pasan: r.passes.length, incompletas: r.incomplete.length,
    };
  });
  // Lighthouse se conecta al mismo navegador, así que usa la sesión ya iniciada (disableStorageReset conserva las cookies)
  const lh = await lighthouse(url, { port: PUERTO, output: "json", logLevel: "error", onlyCategories: ["accessibility"], disableStorageReset: true });
  const cat = lh.lhr.categories.accessibility;
  const fallos = Object.values(lh.lhr.audits).filter((a) => a.score !== null && a.score < 1 && cat.auditRefs.some((r) => r.id === a.id && r.weight > 0))
    .map((a) => ({ id: a.id, titulo: a.title, puntaje: a.score }));
  console.log(`${rol.padEnd(13)} ${ruta.padEnd(28)} Lighthouse ${Math.round(cat.score * 100)}  axe: ${axe.violaciones.length} tipo(s) de hallazgo`);
  return { ruta, rol, url, axe, lighthouse: { puntaje: Math.round(cat.score * 100), fallos } };
}

const resultados = [];
const anonima = await nuevaPagina();
resultados.push(await auditar(anonima, "/login", "sin sesión"));
await anonima.close();

for (const [rol, credenciales, rutas] of [["Administrador", ADMIN, RUTAS_ADMIN], ["Trabajador", TRABAJADOR, RUTAS_TRABAJADOR]]) {
  const pagina = await iniciarSesion(credenciales);
  for (const ruta of rutas) resultados.push(await auditar(pagina, ruta, rol));
  await cerrarSesion(pagina);
  await pagina.close();
}
await navegador.close();
fs.mkdirSync(path.dirname(salida), { recursive: true });
fs.writeFileSync(salida, JSON.stringify(resultados, null, 2));

const puntajes = resultados.map((r) => r.lighthouse.puntaje);
const tipos = {};
for (const r of resultados) for (const v of r.axe.violaciones) tipos[`${v.id} (${v.impacto})`] = (tipos[`${v.id} (${v.impacto})`] || 0) + 1;
console.log(`\nPantallas: ${resultados.length} | Lighthouse mínimo ${Math.min(...puntajes)}, máximo ${Math.max(...puntajes)}, promedio ${(puntajes.reduce((a, b) => a + b, 0) / puntajes.length).toFixed(1)}`);
console.log("Hallazgos de axe por tipo (cantidad de pantallas):", tipos);
process.exit(Object.keys(tipos).length ? 1 : 0); // código distinto de cero si hay algún hallazgo (sirve para automatizarlo)
