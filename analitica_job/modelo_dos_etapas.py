"""
Modelo de dos etapas para predicción de demanda (HU-09), enfoque estándar de
la literatura para demanda intermitente (Croston 1972; Teunter, Syntetos &
Babai 2011 -- método TSB) y equivalente a un "hurdle model" (Cragg 1971)
para datos de conteo con exceso de ceros.

En vez de un solo modelo que aprende simultáneamente "cuándo hay venta" y
"cuánto se vende", se separan ambas preguntas:
    Etapa A (clasificación): probabilidad de que el producto venda algo ese mes.
    Etapa B (regresión): dado que sí vende, cuánto -- entrenada SOLO con los
    meses que tuvieron venta real (para no diluir el patrón con los ceros).
Predicción final = P(venta) * cantidad_esperada_si_hay_venta.

Motivación: en el hito del 80% (recencia + MASE/WAPE), la clase ABC "A"
(alta rotación) quedó como el segmento con peor MASE relativo (0.738) --
son productos que casi siempre venden algo, así que el problema ahí ya no
es tanto "¿vende o no?" sino acertar la cantidad exacta. Un regresor
dedicado solo a esa pregunta, sin la distracción de aprender también los
ceros, podría predecir mejor esas cantidades.

Requiere haber corrido antes: construir_features, segmentar_abc.

Uso:
    python -m analitica_job.modelo_dos_etapas
"""

from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, PoissonRegressor

from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS, cargar_datasets
from analitica_job.metricas import escala_naive_por_producto, mase, resumen_metricas
from analitica_job.modelo_compuesto import ModeloDosEtapas
from analitica_job.preparar_datos import CARPETA_SALIDA

CARPETA_MODELOS = CARPETA_SALIDA.parent / "modelos"
COLUMNA_OBJETIVO = "unidades_vendidas"


def evaluar(nombre: str, conjunto: str, y_real, y_pred, df: pd.DataFrame, escala: pd.Series) -> dict:
    metricas = resumen_metricas(y_real, y_pred)
    fila = {"modelo": nombre, "conjunto": conjunto, **metricas}
    if conjunto == "prueba":
        df_pred = df.assign(_pred=y_pred)
        valor_mase = mase(df_pred, COLUMNA_OBJETIVO, "_pred", escala)
        fila["MASE"] = round(valor_mase, 3) if valor_mase is not None else None
    return fila


def main() -> None:
    CARPETA_MODELOS.mkdir(exist_ok=True)

    train, test = cargar_datasets()
    X_train, y_train = train[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], train[COLUMNA_OBJETIVO]
    X_test, y_test = test[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS], test[COLUMNA_OBJETIVO]
    escala = escala_naive_por_producto(train)

    print(f"Filas de entrenamiento con venta > 0: {(y_train > 0).sum()} de {len(y_train)} "
          f"({(y_train > 0).mean() * 100:.1f}%)")

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
    modelos_entrenados = {}
    for nombre, (clasificador, regresor) in combinaciones.items():
        modelo = ModeloDosEtapas(clasificador, regresor)
        modelo.fit(X_train, y_train)
        modelos_entrenados[nombre] = modelo

        pred_train = modelo.predict(X_train)
        pred_test = modelo.predict(X_test)

        resultados.append(evaluar(nombre, "entrenamiento", y_train, pred_train, train, escala))
        resultados.append(evaluar(nombre, "prueba", y_test, pred_test, test, escala))

    tabla_resultados = pd.DataFrame(resultados)
    print(tabla_resultados.to_string(index=False))
    tabla_resultados.to_csv(CARPETA_SALIDA / "comparacion_modelo_dos_etapas.csv", index=False)

    resultados_prueba = tabla_resultados[tabla_resultados["conjunto"] == "prueba"]
    nombre_mejor = resultados_prueba.sort_values("MASE").iloc[0]["modelo"]
    mase_mejor = resultados_prueba.sort_values("MASE").iloc[0]["MASE"]
    print(f"\nMejor combinación de dos etapas (menor MASE en prueba): {nombre_mejor} (MASE={mase_mejor})")

    # comparacion contra el candidato de una sola etapa del hito anterior (Random Forest + recencia)
    candidato_actual = joblib.load(CARPETA_MODELOS / "modelo_demanda_candidato.joblib")
    pred_actual = np.clip(candidato_actual.predict(X_test), a_min=0, a_max=None)
    fila_actual = evaluar("Random Forest (una etapa, hito 80% actual)", "prueba", y_test, pred_actual, test, escala)
    print(f"\nCandidato actual (una etapa) para comparar: {fila_actual}")

    if mase_mejor < fila_actual["MASE"]:
        print(f"\nEl modelo de dos etapas SUPERA al candidato actual "
              f"({mase_mejor} < {fila_actual['MASE']}). Se guarda como nuevo candidato.")
        joblib.dump(modelos_entrenados[nombre_mejor], CARPETA_MODELOS / "modelo_demanda_candidato_dos_etapas.joblib")
    else:
        print(f"\nEl modelo de dos etapas NO supera al candidato actual "
              f"({mase_mejor} >= {fila_actual['MASE']}). Se conserva el candidato de una etapa.")

    # analisis por clase ABC del mejor modelo de dos etapas, para comparar directo con el hito anterior
    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    mejor_modelo = modelos_entrenados[nombre_mejor]
    test_con_clase = test.merge(segmentacion[["producto_normalizado", "clase_abc"]], on="producto_normalizado", how="left")
    test_con_clase["clase_abc"] = test_con_clase["clase_abc"].fillna("Sin clasificar")
    test_con_clase["prediccion"] = mejor_modelo.predict(test_con_clase[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS])

    print(f"\n=== Métricas por clase ABC ({nombre_mejor}) ===")
    filas_clase = []
    for clase, grupo in test_con_clase.groupby("clase_abc"):
        m = resumen_metricas(grupo["unidades_vendidas"], grupo["prediccion"])
        valor_mase = mase(grupo, "unidades_vendidas", "prediccion", escala)
        m["MASE"] = round(valor_mase, 3) if valor_mase is not None else None
        m["clase_abc"] = clase
        m["n_productos"] = grupo["producto_normalizado"].nunique()
        filas_clase.append(m)
        print(f"  Clase {clase}: MAE={m['MAE']} MAPE={m['MAPE_%']} WAPE={m['WAPE_%']} "
              f"MASE={m['MASE']} ({m['n_productos']} productos)")

    pd.DataFrame(filas_clase)[["clase_abc", "n_productos", "MAE", "MAPE_%", "WAPE_%", "MASE"]].to_csv(
        CARPETA_SALIDA / "metricas_por_clase_abc_dos_etapas.csv", index=False
    )
    print(f"\nGuardado: {CARPETA_SALIDA / 'comparacion_modelo_dos_etapas.csv'}")
    print(f"Guardado: {CARPETA_SALIDA / 'metricas_por_clase_abc_dos_etapas.csv'}")


if __name__ == "__main__":
    main()
