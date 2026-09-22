"""
Segmentación ABC de productos (HU-10, Sprint 3): clasifica cada producto
según su participación acumulada en las ventas en soles de los últimos 12
meses móviles (no un año calendario fijo -- ver memoria del proyecto),
regla de Pareto 80/15/5 (ver app/models/segmentacion_producto.py).

Usa producto_normalizado como identificador (no id_producto): el catálogo
real de MySQL todavía tiene datos de prueba (44 productos genéricos) que no
coinciden con los productos reales del Excel -- la vinculación con
id_producto queda pendiente hasta tener el catálogo maestro real (ver
memoria "preguntas pendientes para el gerente").

Uso:
    python -m analitica_job.segmentar_abc
"""

from __future__ import annotations

import pandas as pd

from analitica_job.construir_features import indice_mes
from analitica_job.preparar_datos import (
    CARPETA_SALIDA,
    ULTIMO_MES_REAL_2026,
    cargar_ventas,
    filtrar_categoria_excluida,
    filtrar_meses_no_confiables,
)

MESES_VENTANA = 12


def filtrar_ventana_movil(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    idx_fin = indice_mes(2026, ULTIMO_MES_REAL_2026)
    idx_inicio = idx_fin - MESES_VENTANA + 1

    idx_fila = indice_mes(df["anio"], df["mes_numero"])
    ventana = df[(idx_fila >= idx_inicio) & (idx_fila <= idx_fin)].copy()
    return ventana, idx_inicio, idx_fin


def clasificar_abc(pct_acumulado: float) -> str:
    if pct_acumulado <= 80:
        return "A"
    if pct_acumulado <= 95:
        return "B"
    return "C"


def calcular_segmentacion(df: pd.DataFrame) -> pd.DataFrame:
    categoria_por_producto = df.groupby("producto_normalizado")["categoria"].agg(lambda s: s.mode().iat[0])

    ventas_por_producto = (
        df.groupby("producto_normalizado")["total_soles_calculado"]
        .sum()
        .rename("ventas_periodo")
        .reset_index()
        .sort_values("ventas_periodo", ascending=False)
        .reset_index(drop=True)
    )

    total_ventas = ventas_por_producto["ventas_periodo"].sum()
    ventas_por_producto["pct_acumulado"] = (
        ventas_por_producto["ventas_periodo"].cumsum() / total_ventas * 100
    ).round(2)
    ventas_por_producto["clase_abc"] = ventas_por_producto["pct_acumulado"].apply(clasificar_abc)
    ventas_por_producto = ventas_por_producto.merge(categoria_por_producto, on="producto_normalizado", how="left")

    return ventas_por_producto[
        ["producto_normalizado", "categoria", "ventas_periodo", "pct_acumulado", "clase_abc"]
    ]


def main() -> None:
    CARPETA_SALIDA.mkdir(exist_ok=True)

    df = cargar_ventas()
    df = filtrar_categoria_excluida(df)
    df = filtrar_meses_no_confiables(df)

    ventana, idx_inicio, idx_fin = filtrar_ventana_movil(df)
    anio_ini, mes_ini = 2025 + (idx_inicio - 1) // 12, (idx_inicio - 1) % 12 + 1
    anio_fin, mes_fin = 2025 + (idx_fin - 1) // 12, (idx_fin - 1) % 12 + 1
    print(f"Ventana móvil de {MESES_VENTANA} meses: {anio_ini}-{mes_ini:02d} a {anio_fin}-{mes_fin:02d}")
    print(f"Filas en la ventana: {len(ventana)}")

    segmentacion = calcular_segmentacion(ventana)
    segmentacion.insert(1, "periodo_inicio", f"{anio_ini}-{mes_ini:02d}-01")
    segmentacion.insert(2, "periodo_fin", f"{anio_fin}-{mes_fin:02d}-01")

    conteo = segmentacion["clase_abc"].value_counts()
    print(f"\nProductos clasificados: {len(segmentacion)}")
    for clase in ["A", "B", "C"]:
        n = conteo.get(clase, 0)
        print(f"  Clase {clase}: {n} productos ({n / len(segmentacion) * 100:.1f}%)")

    segmentacion.to_csv(CARPETA_SALIDA / "segmentacion_abc.csv", index=False)
    print(f"\nGuardado en {CARPETA_SALIDA / 'segmentacion_abc.csv'}")


if __name__ == "__main__":
    main()
