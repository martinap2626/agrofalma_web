"""
Entrenamiento del modelo de predicción de demanda (HU-09). Prueba una línea
base sin aprendizaje y varios modelos de scikit-learn sobre el dataset panel
con rezagos (analitica_job.construir_features), y los compara -- entregable
de la fase Modeling (semana 5, hito 65%) y refinado en la fase Evaluation
(semana 6, hito 80%) del cronograma CRISP-DM.

Métricas: se reportan MAE, MAPE y WAPE, pero el candidato se elige por MASE
(Mean Absolute Scaled Error, Hyndman & Koehler 2006) -- MAPE se indefine con
meses en cero (la mayoría de las filas de este dataset), un problema
documentado en la literatura de demanda intermitente; MASE no tiene ese
problema y compara contra un método ingenuo (repetir el mes anterior), lo
que responde una pregunta más útil que un porcentaje arbitrario: "¿el modelo
le gana a la alternativa más simple posible?".

Uso:
    python -m analitica_job.entrenar_modelo
"""

from __future__ import annotations

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from analitica_job.metricas import escala_naive_por_producto, mase, resumen_metricas
from analitica_job.preparar_datos import CARPETA_SALIDA

CARPETA_MODELOS = CARPETA_SALIDA.parent / "modelos"

COLUMNAS_CATEGORICAS = ["categoria", "campana"]
COLUMNAS_NUMERICAS = ["lag_1", "lag_2", "lag_3", "promedio_movil_3", "recencia", "mes_numero"]
COLUMNA_OBJETIVO = "unidades_vendidas"


def cargar_datasets() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(CARPETA_SALIDA / "dataset_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "dataset_prueba.csv")
    return train, test


def construir_pipeline(modelo) -> Pipeline:
    preprocesador = ColumnTransformer(
        transformers=[("categoricas", OneHotEncoder(handle_unknown="ignore"), COLUMNAS_CATEGORICAS)],
        remainder="passthrough",
    )
    return Pipeline([("preprocesamiento", preprocesador), ("modelo", modelo)])


def evaluar(nombre: str, conjunto: str, y_real, y_pred, test_df: pd.DataFrame, escala: pd.Series) -> dict:
    metricas = resumen_metricas(y_real, y_pred)
    fila = {"modelo": nombre, "conjunto": conjunto, **metricas}
    if conjunto == "prueba":
        df_pred = test_df.assign(_pred=y_pred)
        fila["MASE"] = round(mase(df_pred, COLUMNA_OBJETIVO, "_pred", escala), 3) if mase(
            df_pred, COLUMNA_OBJETIVO, "_pred", escala
        ) is not None else None
    return fila


def main() -> None:
    CARPETA_MODELOS.mkdir(exist_ok=True)

    train, test = cargar_datasets()
    X_train, y_train = train[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], train[COLUMNA_OBJETIVO]
    X_test, y_test = test[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], test[COLUMNA_OBJETIVO]

    escala = escala_naive_por_producto(train)

    resultados = []

    # --- Linea base: "el mes que viene se vende como el promedio de los ultimos 3" ---
    pred_baseline_test = test["promedio_movil_3"]
    resultados.append(evaluar("Linea base (promedio movil 3 meses)", "prueba", y_test, pred_baseline_test, test, escala))

    # --- Candidatos de scikit-learn ---
    # PoissonRegressor esta pensado para variables de conteo (como unidades
    # vendidas): asume una relacion multiplicativa, no aditiva, entre las
    # variables (ej. "campana chica reduce la demanda a la mitad" en vez de
    # "le resta una cantidad fija") -- por eso corrige mejor el sesgo que
    # arrastraba la regresion lineal de un mes de alta demanda al siguiente.
    candidatos = {
        "Regresion lineal": LinearRegression(),
        "Random Forest": RandomForestRegressor(n_estimators=300, random_state=42),
        "Regresion Poisson": PoissonRegressor(max_iter=1000),
    }

    modelos_entrenados = {}
    for nombre, modelo in candidatos.items():
        pipeline = construir_pipeline(modelo)
        pipeline.fit(X_train, y_train)
        modelos_entrenados[nombre] = pipeline

        pred_train = pipeline.predict(X_train)
        pred_test = pipeline.predict(X_test)

        resultados.append(evaluar(nombre, "entrenamiento", y_train, pred_train, train, escala))
        resultados.append(evaluar(nombre, "prueba", y_test, pred_test, test, escala))

    tabla_resultados = pd.DataFrame(resultados)
    print(tabla_resultados.to_string(index=False))
    tabla_resultados.to_csv(CARPETA_SALIDA / "comparacion_modelos.csv", index=False)

    # candidato: el que tenga menor MASE en prueba entre los modelos entrenados (no la linea base)
    resultados_prueba = tabla_resultados[
        (tabla_resultados["conjunto"] == "prueba") & (tabla_resultados["modelo"] != "Linea base (promedio movil 3 meses)")
    ]
    nombre_candidato = resultados_prueba.sort_values("MASE").iloc[0]["modelo"]
    print(f"\nModelo candidato (menor MASE en prueba): {nombre_candidato}")

    joblib.dump(modelos_entrenados[nombre_candidato], CARPETA_MODELOS / "modelo_demanda_candidato.joblib")
    print(f"Guardado en {CARPETA_MODELOS / 'modelo_demanda_candidato.joblib'}")


if __name__ == "__main__":
    main()
