"""
Prueba exploratoria (rama test02): MAPE calculado sobre el TOTAL SEMESTRAL por
producto (campana grande ene-jun 2026) en vez de producto-mes.

Unico semestre completo de prueba posible con los datos reales: ene-jun 2026
(jul-dic 2026 solo tiene jul-ago reales). Para que sea fuera de tiempo, el
modelo se entrena SOLO con 2025 (mes_idx <= 12) y se compara contra:
  - Estacional ingenuo: total ene-jun 2025 repetido.
  - Semestre anterior: total jul-dic 2025 repetido.
  - Modelo dos etapas (RF + Poisson), prediccion recursiva 6 meses (los lags
    se alimentan con las propias predicciones, no con ventas reales futuras).
  - Modelo dos etapas, un paso adelante con lags reales (referencia
    optimista: equivale a re-predecir cada mes con el mes anterior ya conocido).

Uso:
    python -m analitica_job.explorar_mape_semestral
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import PoissonRegressor

from analitica_job.construir_features import campana_de
from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS, COLUMNA_OBJETIVO, cargar_datasets
from analitica_job.metricas import mape, wape
from analitica_job.modelo_compuesto import ModeloDosEtapas
from analitica_job.preparar_datos import CARPETA_SALIDA

IDX_FIN_TRAIN = 12
MESES_TEST = list(range(13, 19))
COLUMNAS_X = COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS


def features_producto(historia: list[float], mes_idx: int, categoria: str) -> dict:
    ultimos = historia[-3:] + [0.0] * max(0, 3 - len(historia))
    lag_1, lag_2, lag_3 = (historia[-i] if len(historia) >= i else 0.0 for i in (1, 2, 3))
    meses_sin_venta = 0
    for venta in reversed(historia):
        if venta > 0:
            break
        meses_sin_venta += 1
    mes_numero = (mes_idx - 1) % 12 + 1
    return {
        "categoria": categoria,
        "campana": campana_de(mes_numero),
        "lag_1": lag_1,
        "lag_2": lag_2,
        "lag_3": lag_3,
        "promedio_movil_3": float(np.mean(historia[-3:])) if len(historia) >= 3 else 0.0,
        "recencia": meses_sin_venta,
        "mes_numero": mes_numero,
    }


def prediccion_recursiva(modelo: ModeloDosEtapas, train: pd.DataFrame) -> pd.Series:
    historias = {p: g.sort_values("mes_idx")[COLUMNA_OBJETIVO].tolist() for p, g in train.groupby("producto_normalizado")}
    categorias = train.groupby("producto_normalizado")["categoria"].agg(lambda s: s.mode().iat[0])
    productos = list(historias)
    totales = dict.fromkeys(productos, 0.0)
    tope = {p: max(h) for p, h in historias.items()}

    for mes_idx in MESES_TEST:
        filas = [features_producto(historias[p], mes_idx, categorias[p]) for p in productos]
        pred = modelo.predict(pd.DataFrame(filas)[COLUMNAS_X])
        for p, valor in zip(productos, pred):
            valor = float(np.nan_to_num(valor, nan=0.0, posinf=tope[p]))
            valor = min(valor, tope[p])
            totales[p] += valor
            historias[p].append(valor)
    return pd.Series(totales)


def resumen(nombre: str, real: pd.Series, pred: pd.Series) -> dict:
    pred = pred.reindex(real.index).fillna(0).clip(lower=0)
    con_venta = real > 0
    ape = ((real - pred).abs() / real)[con_venta] * 100
    return {
        "metodo": nombre,
        "n_productos": int(con_venta.sum()),
        "MAPE_%": round(mape(real, pred), 1),
        "APE_mediano_%": round(float(ape.median()), 1),
        "WAPE_%": round(wape(real, pred), 1),
        "productos_error<=15%": f"{(ape <= 15).mean() * 100:.0f}%",
        "productos_error<=30%": f"{(ape <= 30).mean() * 100:.0f}%",
    }


def main() -> None:
    train_all, test_all = cargar_datasets()
    panel = pd.concat([train_all, test_all], ignore_index=True)

    train = panel[panel["mes_idx"] <= IDX_FIN_TRAIN].copy()
    semestre = panel[panel["mes_idx"].isin(MESES_TEST)]
    print(f"Entrenamiento: solo 2025 ({len(train)} filas, {train['producto_normalizado'].nunique()} productos)")

    real_h1 = semestre.groupby("producto_normalizado")[COLUMNA_OBJETIVO].sum()
    productos_train = set(train["producto_normalizado"])
    real_h1 = real_h1[real_h1.index.isin(productos_train)]
    nuevos = semestre["producto_normalizado"].nunique() - len(real_h1)
    print(f"Productos con venta ene-jun 2026 y con historial 2025: {(real_h1 > 0).sum()} "
          f"(excluidos por ser nuevos en 2026: {nuevos})\n")

    h1_2025 = train[train["mes_idx"] <= 6].groupby("producto_normalizado")[COLUMNA_OBJETIVO].sum()
    h2_2025 = train[train["mes_idx"] > 6].groupby("producto_normalizado")[COLUMNA_OBJETIVO].sum()

    X_train, y_train = train[COLUMNAS_X], train[COLUMNA_OBJETIVO]
    modelo = ModeloDosEtapas(RandomForestClassifier(n_estimators=300, random_state=42), PoissonRegressor(max_iter=1000))
    modelo.fit(X_train, y_train)

    pred_recursiva = prediccion_recursiva(modelo, train)

    un_paso = semestre[semestre["producto_normalizado"].isin(productos_train)].copy()
    un_paso["pred"] = modelo.predict(un_paso[COLUMNAS_X])
    pred_un_paso = un_paso.groupby("producto_normalizado")["pred"].sum()

    metodos = {
        "Estacional ingenuo (ene-jun 2025)": h1_2025,
        "Semestre anterior (jul-dic 2025)": h2_2025,
        "Modelo 2 etapas, recursivo 6 meses": pred_recursiva,
        "Modelo 2 etapas, un paso c/lags reales (optimista)": pred_un_paso,
    }

    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv").set_index("producto_normalizado")["clase_abc"]
    filas = []
    for titulo, filtro in [
        ("TODOS", real_h1.index),
        ("SOLO CLASE A", real_h1.index[real_h1.index.map(segmentacion) == "A"]),
        ("SOLO CLASES A+B", real_h1.index[real_h1.index.map(segmentacion).isin(["A", "B"])]),
    ]:
        real = real_h1.loc[filtro]
        print(f"=== {titulo} ({int((real > 0).sum())} productos con venta) ===")
        tabla = pd.DataFrame([resumen(nombre, real, pred) for nombre, pred in metodos.items()])
        print(tabla.to_string(index=False), "\n")
        tabla.insert(0, "segmento", titulo)
        filas.append(tabla)

    salida = CARPETA_SALIDA / "mape_semestral_ene_jun_2026.csv"
    pd.concat(filas).to_csv(salida, index=False)
    print(f"Guardado: {salida}")


if __name__ == "__main__":
    main()
