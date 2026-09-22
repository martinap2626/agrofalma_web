"""
Análisis de errores del modelo candidato (HU-09), fase Evaluation de CRISP-DM
(semana 6 del cronograma, hito del 80%): "calcular métricas pertinentes,
analizar errores, identificar casos incorrectos, comparar resultados con
criterios de éxito".

Cruza las predicciones del modelo candidato sobre el conjunto de prueba real
(jul-ago 2026) con la clase ABC de cada producto (analitica_job/segmentar_abc.py),
para ver si el error se concentra en los productos de baja rotación (clase C)
o está repartido parejo -- y contrasta el resultado contra la meta de éxito
del proyecto de investigación (MAPE <= 15%).

Requiere haber corrido antes: construir_features, entrenar_modelo, segmentar_abc.

Uso:
    python -m analitica_job.analizar_errores
"""

from __future__ import annotations

import joblib
import numpy as np
import pandas as pd

from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS
from analitica_job.preparar_datos import CARPETA_SALIDA

META_MAPE = 15.0  # criterio de exito del proyecto de investigacion


def calcular_metricas(y_real: np.ndarray, y_pred: np.ndarray) -> dict:
    y_real = np.asarray(y_real, dtype=float)
    y_pred = np.clip(np.asarray(y_pred, dtype=float), a_min=0, a_max=None)

    mae = float(np.mean(np.abs(y_real - y_pred)))
    con_venta = y_real > 0
    if con_venta.sum() > 0:
        mape = float(np.mean(np.abs((y_real[con_venta] - y_pred[con_venta]) / y_real[con_venta])) * 100)
    else:
        mape = float("nan")

    return {
        "MAE": round(mae, 2),
        "MAPE_%": round(mape, 2) if not np.isnan(mape) else None,
        "n_meses_con_venta": int(con_venta.sum()),
        "n_total": len(y_real),
    }


def main() -> None:
    test = pd.read_csv(CARPETA_SALIDA / "dataset_prueba.csv")
    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    modelo = joblib.load(CARPETA_SALIDA.parent / "modelos" / "modelo_demanda_candidato.joblib")

    cols = COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS
    test = test.copy()
    test["prediccion"] = np.clip(modelo.predict(test[cols]), 0, None)
    test["error_abs"] = (test["unidades_vendidas"] - test["prediccion"]).abs()

    con_venta = test["unidades_vendidas"] > 0
    test["error_pct"] = np.nan
    test.loc[con_venta, "error_pct"] = (
        (test.loc[con_venta, "unidades_vendidas"] - test.loc[con_venta, "prediccion"]).abs()
        / test.loc[con_venta, "unidades_vendidas"] * 100
    )

    # cruce con la clase ABC (calculada sobre una ventana de 12 meses que
    # termina en el mismo mes que el conjunto de prueba, asi que casi todos
    # los productos del test deberian tener clase asignada)
    clases = segmentacion[["producto_normalizado", "clase_abc"]]
    test = test.merge(clases, on="producto_normalizado", how="left")
    test["clase_abc"] = test["clase_abc"].fillna("Sin clasificar")

    print("=== Métrica global (ya reportada) ===")
    global_m = calcular_metricas(test["unidades_vendidas"], test["prediccion"])
    print(global_m)

    print("\n=== Métricas por clase ABC ===")
    filas_resumen = []
    for clase, grupo in test.groupby("clase_abc"):
        m = calcular_metricas(grupo["unidades_vendidas"], grupo["prediccion"])
        m["clase_abc"] = clase
        m["n_productos"] = grupo["producto_normalizado"].nunique()
        filas_resumen.append(m)
        print(f"  Clase {clase}: MAE={m['MAE']} MAPE={m['MAPE_%']}% "
              f"(n_meses_con_venta={m['n_meses_con_venta']}/{m['n_total']}, "
              f"{m['n_productos']} productos)")

    resumen = pd.DataFrame(filas_resumen)[
        ["clase_abc", "n_productos", "n_meses_con_venta", "n_total", "MAE", "MAPE_%"]
    ]
    resumen.to_csv(CARPETA_SALIDA / "metricas_por_clase_abc.csv", index=False)

    print(f"\n=== Comparación contra el criterio de éxito (MAPE <= {META_MAPE}%) ===")
    for _, fila in resumen.iterrows():
        if fila["MAPE_%"] is None:
            continue
        cumple = "SI cumple" if fila["MAPE_%"] <= META_MAPE else "no cumple"
        print(f"  Clase {fila['clase_abc']}: {fila['MAPE_%']}% -> {cumple}")

    print("\n=== Top 10 casos con mayor error absoluto (peores casos) ===")
    peores_abs = test.sort_values("error_abs", ascending=False).head(10)
    columnas_mostrar = ["producto_normalizado", "clase_abc", "mes_numero", "unidades_vendidas", "prediccion", "error_abs"]
    print(peores_abs[columnas_mostrar].to_string(index=False))

    test[["producto_normalizado", "categoria", "clase_abc", "anio", "mes_numero",
          "unidades_vendidas", "prediccion", "error_abs", "error_pct"]].to_csv(
        CARPETA_SALIDA / "analisis_errores_detalle.csv", index=False
    )
    print(f"\nGuardado: {CARPETA_SALIDA / 'metricas_por_clase_abc.csv'}")
    print(f"Guardado: {CARPETA_SALIDA / 'analisis_errores_detalle.csv'}")


if __name__ == "__main__":
    main()
