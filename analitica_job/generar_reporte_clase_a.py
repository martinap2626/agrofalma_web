"""
Reporte ad-hoc (fuera del pipeline oficial) para que el usuario analice a mano
los productos de clase A: resumen, categoria y ventas mes a mes.

No se integra a app/ ni a los demas scripts de analitica_job -- es un export
puntual pedido por el usuario, generado a partir de los artefactos ya
calculados por segmentar_abc.py y preparar_datos.py.

Uso:
    python -m analitica_job.generar_reporte_clase_a
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from analitica_job.preparar_datos import CARPETA_SALIDA

SALIDA = CARPETA_SALIDA / "clase_a_ventas_mensuales.xlsx"


def main() -> None:
    seg = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    train = pd.read_csv(CARPETA_SALIDA / "panel_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "panel_prueba.csv")
    panel = pd.concat([train, test], ignore_index=True)

    clase_a = seg[seg["clase_abc"] == "A"].copy()
    clase_a = clase_a.sort_values("ventas_periodo", ascending=False).reset_index(drop=True)
    productos_a = clase_a["producto_normalizado"].tolist()

    periodo_inicio_abc = clase_a["periodo_inicio"].iat[0]
    periodo_fin_abc = clase_a["periodo_fin"].iat[0]

    detalle = panel[panel["producto_normalizado"].isin(productos_a)].copy()

    totales_rango_completo = (
        detalle.groupby("producto_normalizado", as_index=False)
        .agg(
            unidades_total_ene25_ago26=("unidades_vendidas", "sum"),
            ventas_soles_total_ene25_ago26=("ventas_soles", "sum"),
        )
    )

    resumen = clase_a.merge(totales_rango_completo, on="producto_normalizado", how="left")
    resumen = resumen.rename(
        columns={
            "ventas_periodo": f"ventas_soles_ventana_abc_{periodo_inicio_abc}_a_{periodo_fin_abc}",
        }
    )
    resumen.insert(0, "ranking", resumen.index + 1)

    detalle["periodo"] = (
        detalle["anio"].astype(str) + "-" + detalle["mes_numero"].astype(str).str.zfill(2)
    )
    orden_producto = {p: i for i, p in enumerate(productos_a)}
    detalle["_orden"] = detalle["producto_normalizado"].map(orden_producto)
    detalle = detalle.sort_values(["_orden", "anio", "mes_numero"]).drop(columns="_orden")

    detalle_cols = [
        "producto_normalizado",
        "categoria",
        "anio",
        "mes_numero",
        "mes",
        "campana",
        "unidades_vendidas",
        "ventas_soles",
        "n_transacciones",
    ]
    detalle_export = detalle[detalle_cols]

    pivot_soles = detalle.pivot_table(
        index=["producto_normalizado", "categoria"],
        columns="periodo",
        values="ventas_soles",
        aggfunc="sum",
        fill_value=0,
    )
    pivot_soles = pivot_soles.reindex(productos_a, level=0)
    pivot_soles = pivot_soles.reset_index()

    pivot_unidades = detalle.pivot_table(
        index=["producto_normalizado", "categoria"],
        columns="periodo",
        values="unidades_vendidas",
        aggfunc="sum",
        fill_value=0,
    )
    pivot_unidades = pivot_unidades.reindex(productos_a, level=0)
    pivot_unidades = pivot_unidades.reset_index()

    notas = pd.DataFrame(
        {
            "Nota": [
                "Fuente: Dataset/datasets combinados/ventas_2025_2026_combinado.xlsx",
                f"Clase A: {len(productos_a)} productos (de {len(seg)} totales: A={len(seg[seg['clase_abc']=='A'])}, "
                f"B={len(seg[seg['clase_abc']=='B'])}, C={len(seg[seg['clase_abc']=='C'])})",
                f"Segmentacion ABC calculada sobre ventana movil de 12 meses: {periodo_inicio_abc} a {periodo_fin_abc}, "
                "regla de Pareto 80/15/5 sobre ventas en soles (ver analitica_job/segmentar_abc.py).",
                "Detalle y pivotes mensuales cubren todo el rango confiable disponible: enero 2025 a agosto 2026.",
                "Se excluyen septiembre-noviembre 2026 del Excel original: son plantilla copiada de 2025, no ventas reales.",
                "Se excluye la categoria COMIDA BALANCEADA (100% alimento animal, fuera del alcance del modelo agricola).",
                "unidades_vendidas ya aplica la correccion del bug de signo en cantidad_credito (filas 'mixto_negativo_positivo' "
                "usan el maximo de |contado| y |credito| en vez de sumarlos a ciegas).",
                "ventas_soles = total_soles_calculado agregado por producto-mes.",
            ]
        }
    )

    with pd.ExcelWriter(SALIDA, engine="openpyxl") as writer:
        resumen.to_excel(writer, sheet_name="Resumen Clase A", index=False)
        detalle_export.to_excel(writer, sheet_name="Detalle mensual", index=False)
        pivot_soles.to_excel(writer, sheet_name="Pivote mensual (soles)", index=False)
        pivot_unidades.to_excel(writer, sheet_name="Pivote mensual (unidades)", index=False)
        notas.to_excel(writer, sheet_name="Notas", index=False)

    print(f"Productos clase A: {len(productos_a)}")
    print(f"Guardado en: {SALIDA}")


if __name__ == "__main__":
    main()
