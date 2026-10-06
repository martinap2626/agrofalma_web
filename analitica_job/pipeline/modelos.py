"""
Pronóstico de demanda mensual por producto BASE (Sede Pimentel): comparación de
métodos con validación de origen móvil, selección por clase ABC y pronóstico del
mes siguiente.

Idea central: en cada mes de prueba se pronostica usando SOLO lo que se sabía
antes de ese mes (nunca se mira el futuro). Se comparan:
  - ingenuo:       repetir el mes anterior
  - promedio_3/6:  promedio de los últimos 3 o 6 meses
  - estacional:    el mismo mes del año anterior (si hay historia)
  - mezcla:        mitad estacional, mitad promedio_3
  - ml_poisson:    árbol de gradiente con pérdida de Poisson (conteos), con
                   rezagos, recencia (meses sin vender), campaña grande/chica y categoría

Métricas: WAPE y MASE como principales (no se indefinen con meses en cero),
MAPE solo donde hubo venta, y MAPE de la venta TOTAL mensual (nivel agregado).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

METODOS = ["ingenuo", "promedio_3", "promedio_6", "estacional", "mezcla", "ml_poisson"]
MIN_HISTORIA_ML = 3  # meses de historia para armar la primera fila de entrenamiento


@dataclass
class Serie:
    """Matriz productos x meses (unidades), con nombres de producto, categorías y periodos."""
    Y: np.ndarray
    productos: list[str]
    periodos: pd.PeriodIndex
    categoria: np.ndarray  # código numérico por producto

    @property
    def n_meses(self) -> int:
        return self.Y.shape[1]


def construir_serie(panel: pd.DataFrame) -> Serie:
    ancho = panel.pivot(index="base", columns="periodo", values="unidades").sort_index()
    categoria = panel.drop_duplicates("base").set_index("base")["categoria"].reindex(ancho.index)
    codigos = {c: i for i, c in enumerate(sorted(categoria.unique()))}
    return Serie(Y=ancho.to_numpy(dtype=float), productos=ancho.index.tolist(),
                 periodos=pd.PeriodIndex(ancho.columns, freq="M"), categoria=categoria.map(codigos).to_numpy())


# ---------------------------------------------------------------------------
# Métodos simples: pronostican la columna t con las columnas < t
# ---------------------------------------------------------------------------
def pronostico_simple(Y: np.ndarray, t: int, metodo: str) -> np.ndarray:
    hist = Y[:, :t]
    if metodo == "ingenuo":
        return hist[:, -1]
    if metodo == "promedio_3":
        return hist[:, -3:].mean(axis=1)
    if metodo == "promedio_6":
        return hist[:, -6:].mean(axis=1)
    if metodo == "estacional":
        # mismo mes del año anterior; sin historia suficiente, cae al promedio de 3 meses
        return Y[:, t - 12] if t >= 12 else hist[:, -3:].mean(axis=1)
    if metodo == "mezcla":
        return 0.5 * pronostico_simple(Y, t, "estacional") + 0.5 * pronostico_simple(Y, t, "promedio_3")
    raise ValueError(metodo)


# ---------------------------------------------------------------------------
# Método de aprendizaje automático
# ---------------------------------------------------------------------------
def _recencia(hist: np.ndarray, tope: int = 12) -> np.ndarray:
    """Meses desde la última venta (0 = vendió el mes pasado), con tope."""
    con_venta = hist > 0
    atras = np.argmax(con_venta[:, ::-1], axis=1)  # cuántos meses atrás fue la última venta
    return np.where(con_venta.any(axis=1), np.minimum(atras, tope), tope).astype(float)


def caracteristicas(s: Serie, t: int) -> np.ndarray:
    """Matriz de variables para pronosticar el mes t de todos los productos (usa solo meses < t)."""
    Y = s.Y
    hist = Y[:, :t]
    mes = s.periodos[t].month if t < s.n_meses else (s.periodos[-1] + (t - s.n_meses + 1)).month
    lag = lambda k: hist[:, -k] if t >= k else np.zeros(Y.shape[0])
    lag12 = Y[:, t - 12] if t >= 12 else np.full(Y.shape[0], -1.0)  # -1 = sin historia de hace un año
    ult12 = hist[:, -12:]
    columnas = [
        lag(1), lag(2), lag(3), hist[:, -3:].mean(axis=1), hist[:, -6:].mean(axis=1), ult12.mean(axis=1),
        (ult12 > 0).sum(axis=1).astype(float), _recencia(hist), lag12,
        np.full(Y.shape[0], 1.0 if mes <= 6 else 0.0),  # campaña grande (ene-jun)
        np.full(Y.shape[0], math.sin(2 * math.pi * mes / 12)), np.full(Y.shape[0], math.cos(2 * math.pi * mes / 12)),
        s.categoria.astype(float),
    ]
    return np.column_stack(columnas)


def _entrenar_ml(s: Serie, t_fin: int) -> HistGradientBoostingRegressor:
    """Entrena con todos los meses t' < t_fin (objetivo: Y[:, t'], variables: meses < t')."""
    X, y = [], []
    for tp in range(MIN_HISTORIA_ML, t_fin):
        X.append(caracteristicas(s, tp))
        y.append(s.Y[:, tp])
    modelo = HistGradientBoostingRegressor(
        loss="poisson", max_iter=150, learning_rate=0.05, max_depth=3, min_samples_leaf=20,
        l2_regularization=1.0, random_state=2026,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        modelo.fit(np.vstack(X), np.concatenate(y))
    return modelo


def pronostico_ml(s: Serie, t: int, modelo: HistGradientBoostingRegressor | None = None) -> np.ndarray:
    modelo = modelo or _entrenar_ml(s, t)
    return np.clip(modelo.predict(caracteristicas(s, t)), 0, None)


# ---------------------------------------------------------------------------
# Validación de origen móvil
# ---------------------------------------------------------------------------
def validar(s: Serie, n_origenes: int = 6) -> pd.DataFrame:
    """Una fila por (mes de prueba, producto, método) con real y pronóstico."""
    t0 = s.n_meses - n_origenes
    filas = []
    for t in range(t0, s.n_meses):
        pred = {m: pronostico_simple(s.Y, t, m) for m in METODOS if m != "ml_poisson"}
        pred["ml_poisson"] = pronostico_ml(s, t)
        for metodo, valores in pred.items():
            filas.append(pd.DataFrame({
                "periodo": str(s.periodos[t]), "producto": s.productos, "metodo": metodo,
                "real": s.Y[:, t], "pred": np.clip(valores, 0, None),
            }))
    return pd.concat(filas, ignore_index=True)


def _wape(real: np.ndarray, pred: np.ndarray) -> float | None:
    total = real.sum()
    return float(np.abs(real - pred).sum() / total * 100) if total > 0 else None


def _mape(real: np.ndarray, pred: np.ndarray) -> float | None:
    m = real > 0
    return float(np.mean(np.abs(real[m] - pred[m]) / real[m]) * 100) if m.any() else None


def escala_ingenua(s: Serie, hasta: int) -> np.ndarray:
    """MAE del método ingenuo dentro de la historia (columnas < hasta), por producto: denominador del MASE."""
    d = np.abs(np.diff(s.Y[:, :hasta], axis=1)).mean(axis=1)
    d[d == 0] = np.nan
    return d


def metricas(val: pd.DataFrame, s: Serie, clases: pd.Series | None = None) -> pd.DataFrame:
    """Métricas por método (y por clase ABC si se entrega `clases`)."""
    t0 = s.n_meses - val["periodo"].nunique()
    escala = pd.Series(escala_ingenua(s, t0), index=s.productos)
    filas = []
    val = val.copy()
    val["clase"] = val["producto"].map(clases) if clases is not None else "Todas"
    for (metodo, clase), g in val.groupby(["metodo", "clase"]):
        sc = g["producto"].map(escala)
        ok = sc.notna()
        mase = float(((g["real"] - g["pred"]).abs()[ok] / sc[ok]).mean()) if ok.any() else None
        # nivel agregado: venta total de cada mes (suma de todos los productos del grupo)
        tot = g.groupby("periodo")[["real", "pred"]].sum()
        filas.append({
            "metodo": metodo, "clase": clase, "productos": g["producto"].nunique(),
            "WAPE_%": _wape(g["real"].to_numpy(), g["pred"].to_numpy()), "MASE": mase,
            "MAPE_%": _mape(g["real"].to_numpy(), g["pred"].to_numpy()),
            "MAPE_total_mensual_%": _mape(tot["real"].to_numpy(), tot["pred"].to_numpy()),
        })
    return pd.DataFrame(filas)


# preferencia ante empate práctico: el método más simple de explicar
_SIMPLICIDAD = {m: i for i, m in enumerate(["ingenuo", "promedio_3", "promedio_6", "estacional", "mezcla", "ml_poisson"])}


def elegir_metodo_por_clase(met_clase: pd.DataFrame, met_reciente: pd.DataFrame | None = None, margen_wape: float = 2.0) -> dict[str, str]:
    """
    Puntaje = promedio del WAPE en toda la validación y del WAPE en los últimos
    meses (si se entrega `met_reciente`): así un método que acierta mucho en el
    pasado pero falla en lo más reciente no gana solo por el pasado. Gana el menor
    puntaje; si el más complejo no mejora por más de `margen_wape` puntos, se
    prefiere el más simple de explicar.
    """
    tabla = met_clase[["metodo", "clase", "WAPE_%"]].rename(columns={"WAPE_%": "wape_total"})
    if met_reciente is not None:
        tabla = tabla.merge(met_reciente[["metodo", "clase", "WAPE_%"]].rename(columns={"WAPE_%": "wape_reciente"}), on=["metodo", "clase"], how="left")
        tabla["puntaje"] = (tabla["wape_total"] + tabla["wape_reciente"].fillna(tabla["wape_total"])) / 2
    else:
        tabla["puntaje"] = tabla["wape_total"]
    elegido = {}
    for clase, g in tabla.groupby("clase"):
        g = g.dropna(subset=["puntaje"]).sort_values("puntaje")
        if g.empty:
            continue
        candidatos = g[g["puntaje"] <= g.iloc[0]["puntaje"] + margen_wape]
        elegido[clase] = min(candidatos["metodo"], key=lambda m: _SIMPLICIDAD[m])
    return elegido


# ---------------------------------------------------------------------------
# Pronóstico del mes siguiente
# ---------------------------------------------------------------------------
def pronosticar_siguiente(s: Serie, metodo_por_clase: dict[str, str], clases: pd.Series) -> pd.DataFrame:
    """Pronóstico del mes después del último dato, con el método elegido para la clase de cada producto."""
    t = s.n_meses
    # se extiende la serie con un mes vacío solo para poder calcular las variables del mes objetivo
    ext = Serie(Y=np.hstack([s.Y, np.zeros((s.Y.shape[0], 1))]), productos=s.productos,
                periodos=pd.PeriodIndex(list(s.periodos) + [s.periodos[-1] + 1], freq="M"), categoria=s.categoria)
    usados = set(metodo_por_clase.values())
    pred = {m: (pronostico_ml(ext, t) if m == "ml_poisson" else pronostico_simple(ext.Y, t, m)) for m in usados}
    clase = clases.reindex(s.productos).fillna("C").to_numpy()
    valor = np.array([pred[metodo_por_clase.get(c, "mezcla")][i] for i, c in enumerate(clase)])
    return pd.DataFrame({
        "producto": s.productos, "periodo_objetivo": str(ext.periodos[-1]), "pronostico": np.clip(valor, 0, None),
        "metodo": [metodo_por_clase.get(c, "mezcla") for c in clase], "venta_mes_anterior": s.Y[:, -1],
    })
