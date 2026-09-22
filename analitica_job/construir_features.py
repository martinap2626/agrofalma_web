"""
Construye el dataset final para el modelo de predicción de demanda (HU-09):
panel COMPLETO producto-mes (incluye meses sin venta, en cero -- no solo
los meses donde hubo transacciones) con variables de rezago (lags) y
estacionalidad, y lo separa en entrenamiento/prueba.

Reutiliza la carga y el filtrado de analitica_job.preparar_datos (mismo
Excel, mismas exclusiones); aquí se agrega completar el panel (rellenar
ceros) y las features que necesita el modelo.

Uso:
    python -m analitica_job.construir_features
"""

from __future__ import annotations

import pandas as pd

from analitica_job.preparar_datos import (
    CARPETA_SALIDA,
    ULTIMO_MES_ENTRENAMIENTO_2026,
    ULTIMO_MES_REAL_2026,
    calcular_unidades_vendidas,
    cargar_ventas,
    filtrar_categoria_excluida,
    filtrar_meses_no_confiables,
)

ANIO_BASE = 2025
N_LAGS = 3


# indice de mes continuo (enero 2025 = 1, diciembre 2025 = 12, enero 2026 = 13, ...)
# para poder restar/comparar meses sin preocuparse del cambio de año
def indice_mes(anio, mes_numero):
    return (anio - ANIO_BASE) * 12 + mes_numero


def campana_de(mes_numero: int) -> str:
    return "GRANDE" if mes_numero <= 6 else "CHICA"


def agregar_panel_disperso(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    df = df.copy()
    df["unidades_vendidas"] = calcular_unidades_vendidas(df)
    df["mes_idx"] = indice_mes(df["anio"], df["mes_numero"])

    # una categoria por producto (el valor mas frecuente, por si algun
    # producto tuviera una fila mal categorizada)
    categoria_por_producto = df.groupby("producto_normalizado")["categoria"].agg(
        lambda s: s.mode().iat[0]
    )

    panel_disperso = df.groupby(["producto_normalizado", "mes_idx"], as_index=False).agg(
        unidades_vendidas=("unidades_vendidas", "sum"),
        ventas_soles=("total_soles_calculado", "sum"),
    )
    return panel_disperso, categoria_por_producto


# rellena con cero cada mes sin venta, desde el primer mes real del
# producto hasta el ultimo mes real disponible (no antes: un producto no
# "existia" con demanda cero antes de su primera venta registrada)
def completar_meses_sin_venta(panel_disperso: pd.DataFrame, idx_max: int) -> pd.DataFrame:
    partes = []
    for producto, grupo in panel_disperso.groupby("producto_normalizado"):
        idx_min = grupo["mes_idx"].min()
        rango_completo = pd.RangeIndex(idx_min, idx_max + 1)
        columnas_numericas = grupo.set_index("mes_idx")[["unidades_vendidas", "ventas_soles"]]
        grupo_completo = columnas_numericas.reindex(rango_completo, fill_value=0)
        grupo_completo["producto_normalizado"] = producto
        grupo_completo.index.name = "mes_idx"
        partes.append(grupo_completo.reset_index())
    return pd.concat(partes, ignore_index=True)


def agregar_features_temporales(panel: pd.DataFrame, categoria_por_producto: pd.Series) -> pd.DataFrame:
    panel = panel.merge(categoria_por_producto, on="producto_normalizado", how="left")
    panel["anio"] = ANIO_BASE + (panel["mes_idx"] - 1) // 12
    panel["mes_numero"] = (panel["mes_idx"] - 1) % 12 + 1
    panel["campana"] = panel["mes_numero"].apply(campana_de)
    panel = panel.sort_values(["producto_normalizado", "mes_idx"]).reset_index(drop=True)

    # rezagos: ventas de 1, 2 y 3 meses atras del mismo producto
    for lag in range(1, N_LAGS + 1):
        panel[f"lag_{lag}"] = panel.groupby("producto_normalizado")["unidades_vendidas"].transform(
            lambda s, lag=lag: s.shift(lag)
        )

    # promedio movil de los 3 meses anteriores (sin incluir el mes actual, para no filtrar informacion futura)
    panel["promedio_movil_3"] = panel.groupby("producto_normalizado")["unidades_vendidas"].transform(
        lambda s: s.shift(1).rolling(3).mean()
    )

    # los primeros meses de cada producto no tienen 1-3 meses de historia previa;
    # se completan en cero (equivalente a "sin historial previo = sin demanda previa")
    columnas_lag = [f"lag_{i}" for i in range(1, N_LAGS + 1)] + ["promedio_movil_3"]
    panel[columnas_lag] = panel[columnas_lag].fillna(0)

    return panel


# Se probaron dos features adicionales (tendencia = lag_1 - lag_2, e indice
# estacional por categoria calculado con 2025) para intentar bajar el MAPE.
# Ninguna de las dos ayudo en la comparacion real contra el conjunto de
# prueba (ver analitica_job/entrenar_modelo.py y la memoria del proyecto) --
# se probaron y se descartaron a proposito, no se agregan aqui para no
# sumar complejidad sin beneficio comprobado.


def dividir_train_test(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx_corte_train = indice_mes(2026, ULTIMO_MES_ENTRENAMIENTO_2026)
    idx_max_real = indice_mes(2026, ULTIMO_MES_REAL_2026)

    es_train = panel["mes_idx"] <= idx_corte_train
    es_test = (panel["mes_idx"] > idx_corte_train) & (panel["mes_idx"] <= idx_max_real)
    return panel[es_train].copy(), panel[es_test].copy()


def main() -> None:
    CARPETA_SALIDA.mkdir(exist_ok=True)

    df = cargar_ventas()
    df = filtrar_categoria_excluida(df)
    df = filtrar_meses_no_confiables(df)

    panel_disperso, categoria_por_producto = agregar_panel_disperso(df)
    idx_max_real = indice_mes(2026, ULTIMO_MES_REAL_2026)

    panel_completo = completar_meses_sin_venta(panel_disperso, idx_max_real)
    print(f"Panel disperso (solo meses con venta): {len(panel_disperso)} filas")
    print(f"Panel completo (incluye meses en cero): {len(panel_completo)} filas")

    panel_final = agregar_features_temporales(panel_completo, categoria_por_producto)

    columnas = [
        "producto_normalizado", "categoria", "anio", "mes_numero", "mes_idx", "campana",
        "lag_1", "lag_2", "lag_3", "promedio_movil_3",
        "unidades_vendidas", "ventas_soles",
    ]
    panel_final = panel_final[columnas]

    train, test = dividir_train_test(panel_final)
    print(f"Entrenamiento: {len(train)} filas, {train['producto_normalizado'].nunique()} productos")
    print(f"Prueba (holdout real): {len(test)} filas, {test['producto_normalizado'].nunique()} productos")

    train.to_csv(CARPETA_SALIDA / "dataset_entrenamiento.csv", index=False)
    test.to_csv(CARPETA_SALIDA / "dataset_prueba.csv", index=False)
    print(f"Guardado en {CARPETA_SALIDA}")


if __name__ == "__main__":
    main()
