"""
Reporte ad-hoc (fuera del pipeline oficial): top 85 productos por unidades
vendidas y top 85 productos por ingreso en soles, con su comportamiento mes
a mes durante todo el rango confiable disponible (enero 2025 - agosto 2026).

No usa la segmentacion ABC (esto es un ranking directo, no Pareto) ni toca
el Excel original -- reusa el panel ya construido por preparar_datos.py.

Uso:
    python -m analitica_job.generar_top_productos
"""

from __future__ import annotations

import pandas as pd

from analitica_job.preparar_datos import CARPETA_SALIDA

N_TOP = 85


def cargar_panel_completo() -> pd.DataFrame:
    train = pd.read_csv(CARPETA_SALIDA / "panel_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "panel_prueba.csv")
    return pd.concat([train, test], ignore_index=True)


def construir_reporte(panel: pd.DataFrame, columna_ranking: str, nombre_columna_total: str, ruta_salida) -> pd.DataFrame:
    totales = (
        panel.groupby(["producto_normalizado", "categoria"], as_index=False)
        .agg(
            unidades_total=("unidades_vendidas", "sum"),
            ventas_soles_total=("ventas_soles", "sum"),
        )
        .sort_values(columna_ranking, ascending=False)
        .reset_index(drop=True)
    )
    top = totales.head(N_TOP).copy()
    top.insert(0, "ranking", top.index + 1)
    productos_top = top["producto_normalizado"].tolist()

    detalle = panel[panel["producto_normalizado"].isin(productos_top)].copy()
    detalle["periodo"] = detalle["anio"].astype(str) + "-" + detalle["mes_numero"].astype(str).str.zfill(2)
    orden_producto = {p: i for i, p in enumerate(productos_top)}
    detalle["_orden"] = detalle["producto_normalizado"].map(orden_producto)
    detalle = detalle.sort_values(["_orden", "anio", "mes_numero"]).drop(columns="_orden")

    detalle_cols = [
        "producto_normalizado", "categoria", "anio", "mes_numero", "mes", "campana",
        "unidades_vendidas", "ventas_soles", "n_transacciones",
    ]
    detalle_export = detalle[detalle_cols]

    pivot_soles = detalle.pivot_table(
        index=["producto_normalizado", "categoria"], columns="periodo", values="ventas_soles",
        aggfunc="sum", fill_value=0,
    ).reindex(productos_top, level=0).reset_index()

    pivot_unidades = detalle.pivot_table(
        index=["producto_normalizado", "categoria"], columns="periodo", values="unidades_vendidas",
        aggfunc="sum", fill_value=0,
    ).reindex(productos_top, level=0).reset_index()

    notas = pd.DataFrame({
        "Nota": [
            "Fuente: Dataset/datasets combinados/ventas_2025_2026_combinado.xlsx",
            f"Ranking: top {N_TOP} productos por {nombre_columna_total}, sobre el rango confiable completo "
            "(enero 2025 a agosto 2026).",
            "No es segmentacion ABC (Pareto): es un ranking directo por el total de cada producto en el periodo.",
            "Se excluyen septiembre-noviembre 2026 del Excel original: son plantilla copiada de 2025, no ventas reales.",
            "Se excluye la categoria COMIDA BALANCEADA (100% alimento animal, fuera del alcance del modelo agricola).",
            "unidades_vendidas ya aplica la correccion del bug de signo en cantidad_credito (filas "
            "'mixto_negativo_positivo' usan el maximo de |contado| y |credito| en vez de sumarlos a ciegas).",
            "ventas_soles = total_soles_calculado agregado por producto-mes.",
        ]
    })

    with pd.ExcelWriter(ruta_salida, engine="openpyxl") as writer:
        top.to_excel(writer, sheet_name="Resumen top 85", index=False)
        detalle_export.to_excel(writer, sheet_name="Detalle mensual", index=False)
        pivot_soles.to_excel(writer, sheet_name="Pivote mensual (soles)", index=False)
        pivot_unidades.to_excel(writer, sheet_name="Pivote mensual (unidades)", index=False)
        notas.to_excel(writer, sheet_name="Notas", index=False)

    return top


def main() -> None:
    panel = cargar_panel_completo()
    print(f"Productos distintos en el panel (ene2025-ago2026): {panel['producto_normalizado'].nunique()}")

    ruta_unidades = CARPETA_SALIDA / "top_85_unidades.xlsx"
    top_unidades = construir_reporte(panel, "unidades_total", "unidades vendidas", ruta_unidades)
    print(f"\nTop 85 por unidades vendidas -- guardado en {ruta_unidades}")
    print(top_unidades[["ranking", "producto_normalizado", "categoria", "unidades_total", "ventas_soles_total"]].head(5).to_string(index=False))

    ruta_ingresos = CARPETA_SALIDA / "top_85_ingresos.xlsx"
    top_ingresos = construir_reporte(panel, "ventas_soles_total", "ingreso en soles", ruta_ingresos)
    print(f"\nTop 85 por ingreso en soles -- guardado en {ruta_ingresos}")
    print(top_ingresos[["ranking", "producto_normalizado", "categoria", "unidades_total", "ventas_soles_total"]].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
