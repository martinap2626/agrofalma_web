"""
Prueba (rama test02): entrenamiento de los modelos de una sola etapa
restringido SOLO a productos de clase A (ver analitica_job/segmentar_abc.py).

Hipotesis a probar: acotar el universo de productos a la clase A (alta
rotacion, ya identificada en el hito 80% como el segmento con peor MASE
relativo) podria mejorar el error relativo (MAPE/WAPE/MASE) al entrenar y
evaluar solo sobre esos productos, en vez de sobre las 262 SKUs completas.

No reemplaza al pipeline oficial (entrenar_modelo.py): reusa los mismos
datasets ya construidos por construir_features.py (dataset_entrenamiento.csv
/ dataset_prueba.csv) y solo filtra por clase_abc == "A" antes de entrenar.
Los resultados van a un CSV aparte para no pisar comparacion_modelos.csv.

Uso:
    python -m analitica_job.entrenar_modelo_clase_a
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, PoissonRegressor

from analitica_job.entrenar_modelo import (
    COLUMNA_OBJETIVO,
    COLUMNAS_CATEGORICAS,
    COLUMNAS_NUMERICAS,
    cargar_datasets,
    construir_pipeline,
    evaluar,
)
from analitica_job.metricas import escala_naive_por_producto
from analitica_job.preparar_datos import CARPETA_SALIDA


def productos_clase_a() -> set[str]:
    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    return set(segmentacion.loc[segmentacion["clase_abc"] == "A", "producto_normalizado"])


def filtrar_clase_a(df: pd.DataFrame, productos_a: set[str]) -> pd.DataFrame:
    return df[df["producto_normalizado"].isin(productos_a)].copy()


def main() -> None:
    productos_a = productos_clase_a()
    print(f"Productos clase A: {len(productos_a)}")

    train_completo, test_completo = cargar_datasets()
    train = filtrar_clase_a(train_completo, productos_a)
    test = filtrar_clase_a(test_completo, productos_a)
    print(f"Entrenamiento (solo clase A): {len(train)} filas, {train['producto_normalizado'].nunique()} productos")
    print(f"Prueba (solo clase A): {len(test)} filas, {test['producto_normalizado'].nunique()} productos")

    X_train, y_train = train[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], train[COLUMNA_OBJETIVO]
    X_test, y_test = test[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], test[COLUMNA_OBJETIVO]

    # Escala del MASE calculada SOLO sobre el entrenamiento de clase A (no
    # sobre las 262 SKUs) -- coherente con evaluar unicamente este universo.
    escala = escala_naive_por_producto(train)

    resultados = []
    pred_baseline_test = test["promedio_movil_3"]
    resultados.append(
        evaluar("Linea base (promedio movil 3 meses)", "prueba", y_test, pred_baseline_test, test, escala)
    )

    candidatos = {
        "Regresion lineal": LinearRegression(),
        "Random Forest": RandomForestRegressor(n_estimators=300, random_state=42),
        "Regresion Poisson": PoissonRegressor(max_iter=1000),
    }

    for nombre, modelo in candidatos.items():
        pipeline = construir_pipeline(modelo)
        pipeline.fit(X_train, y_train)

        pred_train = pipeline.predict(X_train)
        pred_test = pipeline.predict(X_test)

        resultados.append(evaluar(nombre, "entrenamiento", y_train, pred_train, train, escala))
        resultados.append(evaluar(nombre, "prueba", y_test, pred_test, test, escala))

    tabla_resultados = pd.DataFrame(resultados)
    print(tabla_resultados.to_string(index=False))
    tabla_resultados.to_csv(CARPETA_SALIDA / "comparacion_modelos_clase_a.csv", index=False)
    print(f"\nGuardado en {CARPETA_SALIDA / 'comparacion_modelos_clase_a.csv'}")


if __name__ == "__main__":
    main()
