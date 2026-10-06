"""
Prueba de carga: simula varios usuarios simultáneos usando el sistema (cada
uno inicia sesión y navega/registra ventas como lo haría en la oficina) contra
la base de datos de carga con 25 000 ventas, servida con waitress.

Mide, por nivel de usuarios concurrentes: tiempos de respuesta (mediana, p95,
máximo) por pantalla, tasa de errores, peticiones por segundo, y el consumo de
CPU y memoria del proceso del servidor.

Uso (desde agrofalma_web, con agrofalma_carga ya sembrada):
    python pruebas_carga/prueba_carga.py              # escalera completa
    python pruebas_carga/prueba_carga.py --rapida     # versión corta (humo)
"""

import json
import random
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import psutil
import requests

RAIZ = Path(__file__).resolve().parent
HOY = date.today().isoformat()
HACE_UN_ANIO = (date.today() - timedelta(days=365)).isoformat()
HACE_TRES_ANIOS = (date.today() - timedelta(days=1095)).isoformat()
PUERTO = 5055
BASE = f"http://127.0.0.1:{PUERTO}"
LIMITE_BUENO_S = 5.0  # rúbrica: "menos de 5 segundos"

# (etiqueta, método, ruta, peso) por rol -- la mezcla refleja el uso real descrito en el proyecto
TAREAS_ADMIN = [
    ("Dashboard", "GET", "/dashboard", 4),
    ("Ventas consolidadas", "GET", "/ventas", 3),
    ("Reportes: ventas", "GET", "/reportes?tipo=ventas", 2),
    ("Reportes: productos", "GET", "/reportes?tipo=productos", 2),
    ("Reporte exigente: ventas de 1 año", "GET", "/reportes?tipo=ventas&desde=" + HACE_UN_ANIO + "&hasta=" + HOY, 1),
    ("Reporte exigente: productos de 3 años", "GET", "/reportes?tipo=productos&desde=" + HACE_TRES_ANIOS + "&hasta=" + HOY, 1),
    ("Ventas consolidadas (3 años)", "GET", "/ventas?desde=" + HACE_TRES_ANIOS + "&hasta=" + HOY, 1),
    ("Analítica: recomendaciones", "GET", "/analitica", 3),
    ("Analítica: predicción", "GET", "/analitica/prediccion", 2),
    ("Analítica: segmentación ABC", "GET", "/analitica/segmentacion", 2),
    ("Productos", "GET", "/productos", 2),
    ("Usuarios", "GET", "/usuarios", 1),
    ("Auditoría", "GET", "/auditoria", 1),
    ("Créditos y cobros", "GET", "/ventas/creditos", 1),
]
TAREAS_TRABAJADOR = [
    ("Mis ventas del día", "GET", "/ventas/mis-ventas", 4),
    ("Productos", "GET", "/productos", 3),
    ("Clientes", "GET", "/ventas/clientes", 1),
    ("Créditos y cobros", "GET", "/ventas/creditos", 2),
    ("Nueva venta (formulario)", "GET", "/ventas/nueva", 2),
    ("Registrar venta", "POST", "/ventas/nueva", 3),
]


def percentil(valores, p):
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    return ordenados[min(int(len(ordenados) * p / 100), len(ordenados) - 1)]


def token_csrf(html):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    return m.group(1) if m else ""


class Usuario(threading.Thread):
    def __init__(self, indice, duracion, pensar, resultados, detener):
        super().__init__(daemon=True)
        self.es_admin = indice % 5 == 0  # 1 de cada 5 usuarios es administrador
        self.login = "admin_c" if self.es_admin else f"w{1 + indice % 8}"
        self.clave = "AdminCarga2026" if self.es_admin else "TrabajoCarga2026"
        self.duracion, self.pensar, self.resultados, self.detener = duracion, pensar, resultados, detener
        self.http = requests.Session()
        self.azar = random.Random(indice)

    def medir(self, etiqueta, metodo, ruta, **kw):
        t0 = time.perf_counter()
        try:
            r = self.http.request(metodo, BASE + ruta, timeout=(10, 45), allow_redirects=False, **kw)
            estado = r.status_code
            ok = estado < 400 or (metodo == "POST" and estado in (200,))
        except requests.RequestException:
            r, estado, ok = None, 0, False
        self.resultados.append((etiqueta, time.perf_counter() - t0, ok, estado))
        return r

    def iniciar_sesion(self):
        r = self.http.get(BASE + "/login", timeout=60)
        r = self.http.post(BASE + "/login", data={"usuario": self.login, "contrasena": self.clave,
                                                   "csrf_token": token_csrf(r.text)}, allow_redirects=False, timeout=60)
        return r.status_code == 302

    def run(self):
        if not self.iniciar_sesion():
            self.resultados.append(("Inicio de sesión", 0.0, False, 0))
            return
        tareas = TAREAS_ADMIN if self.es_admin else TAREAS_TRABAJADOR
        pesos = [t[3] for t in tareas]
        fin = time.time() + self.duracion
        while time.time() < fin and not self.detener.is_set():
            etiqueta, metodo, ruta, _ = self.azar.choices(tareas, pesos)[0]
            if metodo == "POST":  # registrar una venta real (escritura en la base de carga)
                formulario = self.http.get(BASE + ruta, timeout=60)
                datos = {
                    "csrf_token": token_csrf(formulario.text), "id_cliente": self.azar.randint(1, 1500), "tipo_venta": self.azar.choice(["contado", "credito"]),
                    "id_producto[]": [self.azar.randint(1, 340) for _ in range(2)], "cantidad[]": ["2", "1"], "precio_unitario[]": ["", ""],
                }
                self.medir(etiqueta, "POST", ruta, data=datos)
            else:
                self.medir(etiqueta, metodo, ruta)
            if self.pensar:
                time.sleep(self.azar.uniform(*self.pensar))


def muestrear_proceso(proceso, muestras, parar):
    conocidos = {proceso.pid: proceso}
    proceso.cpu_percent(None)
    while not parar.is_set():
        time.sleep(0.5)
        try:
            for hijo in proceso.children(recursive=True):
                conocidos.setdefault(hijo.pid, hijo)
            cpu = mem = 0.0
            for p in list(conocidos.values()):
                try:
                    cpu += p.cpu_percent(None)
                    mem += p.memory_info().rss / (1024 * 1024)
                except psutil.NoSuchProcess:
                    conocidos.pop(p.pid, None)
            muestras.append((cpu, mem))
        except psutil.NoSuchProcess:
            return


def ejecutar_nivel(proceso, n_usuarios, duracion, pensar):
    resultados, muestras = [], []
    detener, parar = threading.Event(), threading.Event()
    monitor = threading.Thread(target=muestrear_proceso, args=(proceso, muestras, parar), daemon=True)
    monitor.start()

    t0 = time.perf_counter()
    usuarios = [Usuario(i, duracion, pensar, resultados, detener) for i in range(n_usuarios)]
    for u in usuarios:
        u.start()
    for u in usuarios:
        u.join(duracion + 90)
    transcurrido = time.perf_counter() - t0
    detener.set()
    parar.set()
    monitor.join(2)

    tiempos = [r[1] for r in resultados if r[3] != 0]
    errores = [r for r in resultados if not r[2]]
    por_ruta = defaultdict(list)
    for etiqueta, seg, _, _ in resultados:
        por_ruta[etiqueta].append(seg)

    return {
        "usuarios": n_usuarios, "duracion_s": round(transcurrido, 1), "peticiones": len(resultados),
        "por_segundo": round(len(resultados) / transcurrido, 1),
        "errores": len(errores), "pct_errores": round(100 * len(errores) / max(len(resultados), 1), 2),
        "mediana_s": round(statistics.median(tiempos), 3) if tiempos else 0,
        "p95_s": round(percentil(tiempos, 95), 3), "p99_s": round(percentil(tiempos, 99), 3), "max_s": round(max(tiempos, default=0), 3),
        "cpu_prom_pct": round(statistics.mean(m[0] for m in muestras), 1) if muestras else 0,
        "cpu_max_pct": round(max((m[0] for m in muestras), default=0), 1),
        "mem_max_mb": round(max((m[1] for m in muestras), default=0), 1),
        "rutas": {k: {"n": len(v), "mediana_s": round(statistics.median(v), 3), "p95_s": round(percentil(v, 95), 3), "max_s": round(max(v), 3)}
                  for k, v in sorted(por_ruta.items())},
    }


def main():
    rapida = "--rapida" in sys.argv
    # (usuarios, segundos, tiempo de "pensar" entre acciones): uso normal y luego estrés sin pausa
    escalera = [(3, 10, (0.5, 1.5))] if rapida else [
        (1, 20, (0.5, 1.5)), (5, 25, (0.5, 1.5)), (10, 25, (0.5, 1.5)), (25, 30, (0.5, 1.5)),
        (50, 30, (0.5, 1.5)), (50, 30, None),
    ]

    servidor = subprocess.Popen([sys.executable, str(RAIZ / "servidor_carga.py"), str(PUERTO), "16"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                requests.get(BASE + "/login", timeout=2)
                break
            except requests.RequestException:
                time.sleep(0.5)
        else:
            sys.exit("El servidor no arrancó")

        proceso = psutil.Process(servidor.pid)
        niveles = []
        for n, dur, pensar in escalera:
            etiqueta = f"{n} usuarios" + (" (estrés, sin pausas)" if pensar is None else " (uso normal)")
            print(f"== {etiqueta}, {dur} s ...", flush=True)
            nivel = ejecutar_nivel(proceso, n, dur, pensar)
            nivel["escenario"] = etiqueta
            niveles.append(nivel)
            print(f"   {nivel['peticiones']} peticiones · {nivel['por_segundo']}/s · errores {nivel['pct_errores']}% · "
                  f"mediana {nivel['mediana_s']}s · p95 {nivel['p95_s']}s · máx {nivel['max_s']}s · "
                  f"CPU máx {nivel['cpu_max_pct']}% · RAM máx {nivel['mem_max_mb']} MB", flush=True)
    finally:
        servidor.terminate()
        try:
            servidor.wait(10)
        except subprocess.TimeoutExpired:
            servidor.kill()

    salida = RAIZ / "resultados"
    salida.mkdir(exist_ok=True)
    archivo = salida / f"carga_{datetime.now():%Y%m%d_%H%M%S}{'_rapida' if rapida else ''}.json"
    archivo.write_text(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "niveles": niveles}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResultados guardados en {archivo}")

    peor = max(niveles, key=lambda n: n["p95_s"])
    print(f"Peor p95: {peor['p95_s']} s ({peor['escenario']}) -> {'CUMPLE' if peor['max_s'] < LIMITE_BUENO_S else 'REVISAR'} el límite de {LIMITE_BUENO_S} s (máx {peor['max_s']} s)")


if __name__ == "__main__":
    main()
