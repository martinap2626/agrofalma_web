"""
Primer entrenamiento del modelo de predicción de demanda (HU-09). Prueba
una línea base sin aprendizaje y dos modelos de scikit-learn sobre el
dataset panel con rezagos (analitica_job.construir_features), y compara
sus resultados -- entregable de la fase Modeling (semana 5, hito 65% del
cronograma CRISP-DM): "modelos entrenados + experimentos + comparación de
resultados + modelo candidato".

La evaluación formal (ajuste fino, análisis de errores) corresponde a la
fase de Evaluation (semana 6, hito 80%); aquí basta con dejar evidencia
de los experimentos y elegir un candidato.

Uso:
    python -m analitica_job.entrenar_modelo
"""

from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from analitica_job.preparar_datos import CARPETA_SALIDA

CARPETA_MODELOS = CARPETA_SALIDA.parent / "modelos"

COLUMNAS_CATEGORICAS = ["categoria", "campana"]
COLUMNAS_NUMERICAS = ["lag_1", "lag_2", "lag_3", "promedio_movil_3", "mes_numero"]
COLUMNA_OBJETIVO = "unidades_vendidas"


def cargar_datasets() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(CARPETA_SALIDA / "dataset_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "dataset_prueba.csv")
    return train, test


# MAE sobre todos los meses, y MAPE solo sobre meses que sí tuvieron venta real
# (con demanda intermitente -- muchos meses en cero -- el MAPE no se puede
# calcular contra un real de 0, es una division entre cero)
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
        "MAPE_%_solo_meses_con_venta": round(mape, 2) if not np.isnan(mape) else None,
        "n_meses_con_venta": int(con_venta.sum()),
        "n_total": len(y_real),
    }


def construir_pipeline(modelo) -> Pipeline:
    preprocesador = ColumnTransformer(
        transformers=[("categoricas", OneHotEncoder(handle_unknown="ignore"), COLUMNAS_CATEGORICAS)],
        remainder="passthrough",
    )
    return Pipeline([("preprocesamiento", preprocesador), ("modelo", modelo)])


def main() -> None:
    CARPETA_MODELOS.mkdir(exist_ok=True)

    train, test = cargar_datasets()
    X_train, y_train = train[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], train[COLUMNA_OBJETIVO]
    X_test, y_test = test[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], test[COLUMNA_OBJETIVO]

    resultados = []

    # --- Linea base: "el mes que viene se vende como el promedio de los ultimos 3" ---
    # no se entrena nada, solo se usa la columna que ya viene calculada
    pred_baseline_test = test["promedio_movil_3"]
    metricas = calcular_metricas(y_test, pred_baseline_test)
    resultados.append({"modelo": "Linea base (promedio movil 3 meses)", "conjunto": "prueba", **metricas})

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

        resultados.append({"modelo": nombre, "conjunto": "entrenamiento", **calcular_metricas(y_train, pred_train)})
        resultados.append({"modelo": nombre, "conjunto": "prueba", **calcular_metricas(y_test, pred_test)})

    tabla_resultados = pd.DataFrame(resultados)
    print(tabla_resultados.to_string(index=False))
    tabla_resultados.to_csv(CARPETA_SALIDA / "comparacion_modelos.csv", index=False)

    # candidato: el que tenga menor MAPE en prueba entre los modelos entrenados (no la linea base)
    # -- MAPE y no MAE porque es la metrica de exito declarada en el proyecto de tesis (meta <=15%)
    resultados_prueba = tabla_resultados[
        (tabla_resultados["conjunto"] == "prueba") & (tabla_resultados["modelo"] != "Linea base (promedio movil 3 meses)")
    ]
    nombre_candidato = resultados_prueba.sort_values("MAPE_%_solo_meses_con_venta").iloc[0]["modelo"]
    print(f"\nModelo candidato (menor MAPE en prueba): {nombre_candidato}")

    joblib.dump(modelos_entrenados[nombre_candidato], CARPETA_MODELOS / "modelo_demanda_candidato.joblib")
    print(f"Guardado en {CARPETA_MODELOS / 'modelo_demanda_candidato.joblib'}")


if __name__ == "__main__":
    main()
