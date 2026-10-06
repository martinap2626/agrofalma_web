"""
Prueba (rama test02): modelo de dos etapas (ver modelo_dos_etapas.py)
restringido SOLO a productos de clase A. Ver
analitica_job/entrenar_modelo_clase_a.py para la motivacion del filtro.

No reemplaza al pipeline oficial ni sobreescribe el candidato guardado en
analitica_job/modelos/ -- es un experimento aparte, con su propio CSV de
resultados.

Uso:
    python -m analitica_job.modelo_dos_etapas_clase_a
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, PoissonRegressor

from analitica_job.entrenar_modelo import COLUMNA_OBJETIVO, COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS, cargar_datasets
from analitica_job.entrenar_modelo_clase_a import filtrar_clase_a, productos_clase_a
from analitica_job.metricas import escala_naive_por_producto, mase, resumen_metricas
from analitica_job.modelo_compuesto import ModeloDosEtapas
from analitica_job.preparar_datos import CARPETA_SALIDA


def evaluar(nombre: str, conjunto: str, y_real, y_pred, df: pd.DataFrame, escala: pd.Series) -> dict:
    metricas = resumen_metricas(y_real, y_pred)
    fila = {"modelo": nombre, "conjunto": conjunto, **metricas}
    if conjunto == "prueba":
        df_pred = df.assign(_pred=y_pred)
        valor_mase = mase(df_pred, COLUMNA_OBJETIVO, "_pred", escala)
        fila["MASE"] = round(valor_mase, 3) if valor_mase is not None else None
    return fila


def main() -> None:
    productos_a = productos_clase_a()

    train_completo, test_completo = cargar_datasets()
    train = filtrar_clase_a(train_completo, productos_a)
    test = filtrar_clase_a(test_completo, productos_a)

    X_train, y_train = train[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], train[COLUMNA_OBJETIVO]
    X_test, y_test = test[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], test[COLUMNA_OBJETIVO]
    escala = escala_naive_por_producto(train)

    print(
        f"Filas de entrenamiento (clase A) con venta > 0: {(y_train > 0).sum()} de {len(y_train)} "
        f"({(y_train > 0).mean() * 100:.1f}%)"
    )

    combinaciones = {
        "Clasificador=RandomForest + Regresor=Poisson": (
            RandomForestClassifier(n_estimators=300, random_state=42),
            PoissonRegressor(max_iter=1000),
        ),
        "Clasificador=RandomForest + Regresor=RandomForest": (
            RandomForestClassifier(n_estimators=300, random_state=42),
            RandomForestRegressor(n_estimators=300, random_state=42),
        ),
        "Clasificador=LogisticRegression + Regresor=Poisson": (
            LogisticRegression(max_iter=1000),
            PoissonRegressor(max_iter=1000),
        ),
        "Clasificador=LogisticRegression + Regresor=LinearRegression": (
            LogisticRegression(max_iter=1000),
            LinearRegression(),
        ),
    }

    resultados = []
    for nombre, (clasificador, regresor) in combinaciones.items():
        modelo = ModeloDosEtapas(clasificador, regresor)
        modelo.fit(X_train, y_train)

        pred_train = modelo.predict(X_train)
        pred_test = modelo.predict(X_test)

        resultados.append(evaluar(nombre, "entrenamiento", y_train, pred_train, train, escala))
        resultados.append(evaluar(nombre, "prueba", y_test, pred_test, test, escala))

    tabla_resultados = pd.DataFrame(resultados)
    print(tabla_resultados.to_string(index=False))
    tabla_resultados.to_csv(CARPETA_SALIDA / "comparacion_modelo_dos_etapas_clase_a.csv", index=False)

    resultados_prueba = tabla_resultados[tabla_resultados["conjunto"] == "prueba"]
    mejor = resultados_prueba.sort_values("MASE").iloc[0]
    print(
        f"\nMejor combinacion (clase A, menor MASE en prueba): {mejor['modelo']} "
        f"(MASE={mejor['MASE']}, MAPE={mejor['MAPE_%']}, WAPE={mejor['WAPE_%']})"
    )
    print(f"\nGuardado en {CARPETA_SALIDA / 'comparacion_modelo_dos_etapas_clase_a.csv'}")


if __name__ == "__main__":
    main()
