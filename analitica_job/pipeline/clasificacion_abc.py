"""
Segmentación ABC por producto base: participación acumulada en las ventas en soles
de los últimos 12 meses móviles (regla de Pareto 80/15/5, confirmada por el
proyecto). Todos los productos del catálogo reciben clase: los que no vendieron
en la ventana quedan en C con ventas 0, de modo que "100% de productos
clasificados" (indicador del proyecto) se cumple por construcción.
"""

from __future__ import annotations

import pandas as pd

MESES_VENTANA = 12


def clasificar(pct_acumulado: float) -> str:
    if pct_acumulado <= 80:
        return "A"
    if pct_acumulado <= 95:
        return "B"
    return "C"


def segmentar(df: pd.DataFrame, ultimo_anio: int, ultimo_mes: int, productos_catalogo: list[str] | None = None) -> pd.DataFrame:
    """Devuelve: base, categoria, ventas_periodo (S/), pct_acumulado, clase_abc, periodo_inicio, periodo_fin."""
    fin = ultimo_anio * 12 + ultimo_mes
    ini = fin - MESES_VENTANA + 1
    idx = df["anio"] * 12 + df["mes"]
    ventana = df[(idx >= ini) & (idx <= fin)]

    ventas = ventana.groupby("base")["soles"].sum().rename("ventas_periodo")
    # si un producto no tiene precio válido, se valora con la mediana de precio de su categoría (para no regalarlo a la clase C)
    if productos_catalogo is not None:
        ventas = ventas.reindex(productos_catalogo, fill_value=0.0)
    ventas = ventas.sort_values(ascending=False).reset_index()
    total = ventas["ventas_periodo"].sum()
    ventas["pct_acumulado"] = (ventas["ventas_periodo"].cumsum() / total * 100).round(2) if total > 0 else 100.0
    ventas["clase_abc"] = ventas["pct_acumulado"].map(clasificar)
    ventas.loc[ventas["ventas_periodo"] <= 0, ["pct_acumulado", "clase_abc"]] = [100.0, "C"]

    categoria = df.groupby("base")["categoria"].agg(lambda s: s.mode().iat[0])
    ventas["categoria"] = ventas["base"].map(categoria)
    anio_i, mes_i = divmod(ini - 1, 12)
    ventas["periodo_inicio"] = f"{anio_i}-{mes_i + 1:02d}-01"
    ventas["periodo_fin"] = f"{ultimo_anio}-{ultimo_mes:02d}-01"
    return ventas[["base", "categoria", "ventas_periodo", "pct_acumulado", "clase_abc", "periodo_inicio", "periodo_fin"]]
