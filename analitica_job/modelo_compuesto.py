"""
Clase del modelo de dos etapas (ver analitica_job/modelo_dos_etapas.py para
el entrenamiento y la comparación de experimentos). Vive en su propio
archivo, aparte del script que se ejecuta con `python -m`, para que joblib
pueda deserializarla correctamente en otros procesos -- si la clase vive en
el script ejecutado como __main__, joblib la guarda con módulo "__main__" y
falla al cargarla desde cualquier otro script.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS


def construir_preprocesador() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[("categoricas", OneHotEncoder(handle_unknown="ignore"), COLUMNAS_CATEGORICAS)],
        remainder="passthrough",
    )


class ModeloDosEtapas:
    """Combina un clasificador (P(venta)) con un regresor entrenado solo en
    filas con venta > 0. predict() devuelve la esperanza P(venta) * cantidad."""

    def __init__(self, clasificador, regresor):
        self.pipeline_clas = Pipeline([("prep", construir_preprocesador()), ("modelo", clasificador)])
        self.pipeline_reg = Pipeline([("prep", construir_preprocesador()), ("modelo", regresor)])

    def fit(self, X: pd.DataFrame, y: pd.Series):
        y_binaria = (y > 0).astype(int)
        self.pipeline_clas.fit(X, y_binaria)

        mask_con_venta = y > 0
        self.pipeline_reg.fit(X[mask_con_venta], y[mask_con_venta])
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        prob_venta = self.pipeline_clas.predict_proba(X)[:, 1]
        cantidad_si_hay_venta = np.clip(self.pipeline_reg.predict(X), a_min=0, a_max=None)
        return prob_venta * cantidad_si_hay_venta
