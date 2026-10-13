"""
Prueba de crecimiento: ¿se degrada el sistema cuando el negocio crece?

Siembra la base APARTE agrofalma_carga con volúmenes crecientes de ventas (1 000, 5 000, 25 000 y 100 000; la empresa registra hoy
unos pocos cientos) y, en cada volumen, mide el tiempo de respuesta de las pantallas principales de un usuario Administrador
(mediana y percentil 95 de varias repeticiones). Sirve de evidencia para los criterios de Flexibilidad (soporta el crecimiento sin
degradación) y Eficiencia (menos de 5 segundos).

Nunca toca agrofalma_bd. Al terminar deja agrofalma_carga con 25 000 ventas, que es el volumen que usa prueba_carga.py.

Uso (desde agrofalma_web):  python pruebas_carga/curva_crecimiento.py
"""

import json
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sembrar_volumen as sv  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

VOLUMENES = [1_000, 5_000, 25_000, 100_000]
REPETICIONES = 15
LIMITE_BUENO_S = 5.0
HOY = date.today().isoformat()
HACE_UN_ANIO = (date.today() - timedelta(days=365)).isoformat()
HACE_TRES_ANIOS = (date.today() - timedelta(days=1095)).isoformat()
PANTALLAS = [
    ("Dashboard", "/dashboard"),
    ("Ventas consolidadas", "/ventas"),
    ("Reporte de ventas de 1 año", f"/reportes?tipo=ventas&desde={HACE_UN_ANIO}&hasta={HOY}"),
    ("Reporte de productos de 3 años", f"/reportes?tipo=productos&desde={HACE_TRES_ANIOS}&hasta={HOY}"),
    ("Analítica: recomendaciones", "/analitica"),
    ("Analítica: predicción", "/analitica/prediccion"),
    ("Ficha de un producto", "/analitica/producto/1"),
    ("Simulador de escenarios", "/analitica/simulador?id_producto=1&escenario=mas&valor_mas=30&stock=40"),
    ("Inventario", "/inventario"),
    ("Parámetros", "/parametros"),
    ("Auditoría", "/auditoria"),
]


def percentil(valores, p):
    ordenados = sorted(valores)
    return ordenados[min(int(len(ordenados) * p / 100), len(ordenados) - 1)]


def medir(volumen):
    sv.N_VENTAS = volumen
    sv.crear_bd()
    app = create_app(sv.ConfigCarga)
    with app.app_context():
        db.create_all()
        t0 = time.perf_counter()
        ventas, detalles, _ = sv.sembrar()
        siembra_s = time.perf_counter() - t0
    cliente = app.test_client()
    html = cliente.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    r = cliente.post("/login", data={"usuario": "admin_c", "contrasena": sv.CLAVE_ADMIN, "csrf_token": token.group(1)})
    assert r.status_code == 302, "no se pudo iniciar sesión en la base de carga"
    filas = []
    for nombre, ruta in PANTALLAS:
        tiempos, estado = [], None
        for _ in range(REPETICIONES):
            t0 = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append(time.perf_counter() - t0)
            estado = respuesta.status_code
        filas.append({"pantalla": nombre, "estado": estado, "mediana_s": round(statistics.median(tiempos), 3),
                      "p95_s": round(percentil(tiempos, 95), 3), "max_s": round(max(tiempos), 3)})
    return {"ventas": ventas, "lineas_de_detalle": detalles, "siembra_s": round(siembra_s, 1), "pantallas": filas}


def main():
    resultados = []
    for volumen in VOLUMENES:
        print(f"== {volumen} ventas ...", flush=True)
        r = medir(volumen)
        resultados.append(r)
        peor = max(r["pantallas"], key=lambda f: f["p95_s"])
        print(f"   lineas {r['lineas_de_detalle']}, peor pantalla: {peor['pantalla']} con p95 {peor['p95_s']} s", flush=True)
    if VOLUMENES[-1] != 25_000:
        print("== dejando agrofalma_carga con 25 000 ventas ...", flush=True)
        sv.N_VENTAS = 25_000
        sv.crear_bd()
        app = create_app(sv.ConfigCarga)
        with app.app_context():
            db.create_all()
            sv.sembrar()
    carpeta = Path(__file__).resolve().parent / "resultados"
    carpeta.mkdir(exist_ok=True)
    salida = carpeta / f"crecimiento_{datetime.now():%Y%m%d_%H%M%S}.json"
    salida.write_text(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "repeticiones": REPETICIONES,
                                  "volumenes": resultados}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nResultados guardados en {salida}")
    print("\npantalla".ljust(34) + "".join(f"{r['ventas']:>10,}".replace(",", " ") for r in resultados) + "   (p95, segundos)")
    for i, (nombre, _) in enumerate(PANTALLAS):
        print(nombre.ljust(33) + "".join(f"{r['pantallas'][i]['p95_s']:>10.3f}" for r in resultados))
    peor_global = max(f["p95_s"] for r in resultados for f in r["pantallas"])
    print(f"\nPeor p95 de todas las pruebas: {peor_global} s (límite de la rúbrica: {LIMITE_BUENO_S} s)")


if __name__ == "__main__":
    main()
