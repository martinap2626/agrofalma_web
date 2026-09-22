"""
Métricas de evaluación pensadas para demanda intermitente (muchos meses en
cero). El MAPE clásico se indefine cuando el valor real es cero -- con este
dataset eso pasa en la mayoría de las filas de prueba -- por eso WAPE y MASE
son las métricas principales, siguiendo Hyndman (2006) "Another look at
forecast-accuracy metrics for intermittent demand" (mismo autor que la
referencia [7] Hyndman & Athanasopoulos ya citada en la tesis).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def mae(y_real, y_pred) -> float:
    y_real = np.asarray(y_real, dtype=float)
    y_pred = np.clip(np.asarray(y_pred, dtype=float), a_min=0, a_max=None)
    return float(np.mean(np.abs(y_real - y_pred)))


def mape(y_real, y_pred) -> float | None:
    y_real = np.asarray(y_real, dtype=float)
    y_pred = np.clip(np.asarray(y_pred, dtype=float), a_min=0, a_max=None)
    con_venta = y_real > 0
    if con_venta.sum() == 0:
        return None
    return float(np.mean(np.abs((y_real[con_venta] - y_pred[con_venta]) / y_real[con_venta])) * 100)


def wape(y_real, y_pred) -> float | None:
    """Weighted Absolute Percentage Error: suma de errores absolutos / suma de ventas reales.
    A diferencia del MAPE, no se indefine con ceros individuales (solo si la suma total es 0)."""
    y_real = np.asarray(y_real, dtype=float)
    y_pred = np.clip(np.asarray(y_pred, dtype=float), a_min=0, a_max=None)
    total_real = y_real.sum()
    if total_real == 0:
        return None
    return float(np.sum(np.abs(y_real - y_pred)) / total_real * 100)


def escala_naive_por_producto(train: pd.DataFrame) -> pd.Series:
    """MAE del metodo ingenuo (repetir el valor del mes anterior) en
    entrenamiento, por producto -- denominador del MASE (Hyndman & Koehler,
    2006). Se calcula en entrenamiento (in-sample) porque siempre hay
    suficientes datos, a diferencia de calcularlo en el conjunto de prueba."""

    def _mae_naive(grupo: pd.DataFrame) -> float:
        valores = grupo.sort_values("mes_idx")["unidades_vendidas"].values
        if len(valores) < 2:
            return np.nan
        return float(np.mean(np.abs(np.diff(valores))))

    return train.groupby("producto_normalizado").apply(_mae_naive)


def mase(test: pd.DataFrame, columna_real: str, columna_pred: str, escala_por_producto: pd.Series) -> float | None:
    """Mean Absolute Scaled Error. <1 = mejor que el metodo ingenuo (repetir
    el mes anterior); >1 = peor. Nunca da infinito salvo que un producto
    tenga ventas exactamente constantes en todo el entrenamiento."""
    escala = test["producto_normalizado"].map(escala_por_producto)
    valido = escala.notna() & (escala > 0)
    if valido.sum() == 0:
        return None
    y_pred_clip = test.loc[valido, columna_pred].clip(lower=0)
    errores_escalados = (test.loc[valido, columna_real] - y_pred_clip).abs() / escala[valido]
    return float(errores_escalados.mean())


def resumen_metricas(y_real, y_pred) -> dict:
    return {"MAE": round(mae(y_real, y_pred), 2), "MAPE_%": mape(y_real, y_pred), "WAPE_%": wape(y_real, y_pred)}
