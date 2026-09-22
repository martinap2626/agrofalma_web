"""
Análisis de errores del modelo candidato (HU-09), fase Evaluation de CRISP-DM
(semana 6 del cronograma, hito del 80%): "calcular métricas pertinentes,
analizar errores, identificar casos incorrectos, comparar resultados con
criterios de éxito".

Cruza las predicciones del modelo candidato sobre el conjunto de prueba real
(jul-ago 2026) con la clase ABC de cada producto (analitica_job/segmentar_abc.py),
para ver si el error se concentra en algún segmento de rotación -- y compara
contra dos criterios: el MAPE<=15% declarado en el proyecto de investigación
(referencial, ver analitica_job/metricas.py sobre por qué no es ideal aquí) y
el MASE<1 (¿le gana al método ingenuo de repetir el mes anterior?).

Requiere haber corrido antes: construir_features, entrenar_modelo, segmentar_abc.

Uso:
    python -m analitica_job.analizar_errores
"""

from __future__ import annotations

import joblib
import numpy as np
import pandas as pd

from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS
from analitica_job.metricas import escala_naive_por_producto, mase, resumen_metricas
from analitica_job.preparar_datos import CARPETA_SALIDA

META_MAPE = 15.0  # criterio de exito declarado en el proyecto de investigacion (referencial)


def main() -> None:
    train = pd.read_csv(CARPETA_SALIDA / "dataset_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "dataset_prueba.csv")
    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    modelo = joblib.load(CARPETA_SALIDA.parent / "modelos" / "modelo_demanda_candidato.joblib")
    escala = escala_naive_por_producto(train)

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

    print("=== Métrica global ===")
    global_m = resumen_metricas(test["unidades_vendidas"], test["prediccion"])
    global_m["MASE"] = round(mase(test, "unidades_vendidas", "prediccion", escala), 3)
    print(global_m)

    print("\n=== Métricas por clase ABC ===")
    filas_resumen = []
    for clase, grupo in test.groupby("clase_abc"):
        m = resumen_metricas(grupo["unidades_vendidas"], grupo["prediccion"])
        m_mase = mase(grupo, "unidades_vendidas", "prediccion", escala)
        m["MASE"] = round(m_mase, 3) if m_mase is not None else None
        m["clase_abc"] = clase
        m["n_productos"] = grupo["producto_normalizado"].nunique()
        filas_resumen.append(m)
        print(f"  Clase {clase}: MAE={m['MAE']} MAPE={m['MAPE_%']} WAPE={m['WAPE_%']} "
              f"MASE={m['MASE']} ({m['n_productos']} productos)")

    resumen = pd.DataFrame(filas_resumen)[["clase_abc", "n_productos", "MAE", "MAPE_%", "WAPE_%", "MASE"]]
    resumen.to_csv(CARPETA_SALIDA / "metricas_por_clase_abc.csv", index=False)

    print(f"\n=== Comparación contra los criterios ===")
    for _, fila in resumen.iterrows():
        veredicto_mape = "SI cumple" if (fila["MAPE_%"] is not None and fila["MAPE_%"] <= META_MAPE) else "no cumple"
        veredicto_mase = "le gana al ingenuo" if (fila["MASE"] is not None and fila["MASE"] < 1) else "pierde contra el ingenuo"
        print(f"  Clase {fila['clase_abc']}: MAPE<=15% -> {veredicto_mape} | MASE<1 -> {veredicto_mase}")

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
