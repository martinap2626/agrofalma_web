"""
Catálogo de productos BASE y sus presentaciones, construido solo a partir de los
Excel de ventas de la Sede Pimentel (el gerente pidió sacar el catálogo de ahí).

Un producto base es el nombre comercial sin el tamaño (ej. "AGRO RAZ"); las
presentaciones son los tamaños en que se vende (250 ML, 1 L, 4 L galón, 50 KG...).
Ver la decisión de diseño en el documento de la sesión: el producto base es lo
que se predice y clasifica; la presentación es lo que se vende y se repone.

Genera un Excel de revisión para el gerente y un DataFrame que carga el comando
`flask cargar-catalogo-base`.

Uso:  python -m analitica_job.pipeline.catalogo
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from analitica_job.pipeline.limpieza import ULTIMO_ANIO_REAL, ULTIMO_MES_REAL, SIN_PRESENTACION, cargar_ventas_limpias

RAIZ_JOB = Path(__file__).resolve().parent.parent
SALIDA = RAIZ_JOB / "datos_procesados"
LISTADO_ANTERIOR = SALIDA / "listado_productos_base.xlsx"  # notas de revisión ya redactadas (si existe)

PREFIJO_CATEGORIA = {"QUIMICOS": "QUI", "FOLIARES": "FOL", "FERTILIZANTES": "FER"}
NOMBRE_CATEGORIA = {"QUIMICOS": "Químicos", "FOLIARES": "Foliares", "FERTILIZANTES": "Fertilizantes"}
MESES_VENTANA = 12


def _indice_mes(anio, mes):
    return anio * 12 + mes


# precio más frecuente entre los positivos, y ante empate el más reciente
def _precio_de_referencia(grupo: pd.DataFrame) -> float | None:
    validos = grupo[grupo["precio"] > 0].sort_values(["anio", "mes"])
    if validos.empty:
        return None
    frecuencias = validos["precio"].value_counts()
    mas_frecuentes = frecuencias[frecuencias == frecuencias.max()].index
    return float(validos[validos["precio"].isin(mas_frecuentes)].iloc[-1]["precio"])


def construir_catalogo(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Devuelve las hojas: productos, presentaciones, nombres, por_confirmar."""
    fin = _indice_mes(ULTIMO_ANIO_REAL, ULTIMO_MES_REAL)
    df = df.copy()
    df["en_ventana"] = _indice_mes(df["anio"], df["mes"]) > fin - MESES_VENTANA

    # --- presentaciones -------------------------------------------------------
    filas = []
    for (base, pres), g in df.groupby(["base", "presentacion"]):
        v = g[g["en_ventana"]]
        filas.append({
            "producto_base": base, "presentacion": pres,
            "unidades_ult_12m": round(float(v["unidades"].sum()), 1),
            "lineas_venta_total": int(len(g)),
            "precio_referencia": _precio_de_referencia(v if len(v) else g),
            "primer_mes": f"{int(g['anio'].min())}-{int(g.loc[g['anio'] == g['anio'].min(), 'mes'].min()):02d}",
            "ultimo_mes": f"{int(g['anio'].max())}-{int(g.loc[g['anio'] == g['anio'].max(), 'mes'].max()):02d}",
            "nombres_escritos_asi": " | ".join(sorted(g["producto_original"].unique())),
        })
    presentaciones = pd.DataFrame(filas).sort_values(["producto_base", "unidades_ult_12m"], ascending=[True, False]).reset_index(drop=True)

    # --- productos base ---------------------------------------------------------
    categoria = df.groupby("base")["categoria"].agg(lambda s: s.mode().iat[0])
    filas = []
    for base, g in df.groupby("base"):
        v = g[g["en_ventana"]]
        pres = presentaciones[presentaciones["producto_base"] == base]
        mas_vendida = pres.iloc[0] if len(pres) else None
        meses_con_venta = v.groupby(["anio", "mes"]).ngroups
        filas.append({
            "producto_base": base, "categoria": categoria[base],
            "n_presentaciones": int(len(pres)),
            "presentaciones": " ; ".join(pres["presentacion"]),
            "unidades_ult_12m": round(float(v["unidades"].sum()), 1),
            "soles_ult_12m": round(float(v["soles"].sum()), 2),
            "meses_con_venta_ult_12m": int(meses_con_venta),
            "precio_referencia": None if mas_vendida is None else mas_vendida["precio_referencia"],
            "primer_mes": f"{int(g['anio'].min())}-{int(g.loc[g['anio'] == g['anio'].min(), 'mes'].min()):02d}",
            "ultimo_mes": f"{int(g['anio'].max())}-{int(g.loc[g['anio'] == g['anio'].max(), 'mes'].max()):02d}",
            "estado_sugerido": "Activo" if meses_con_venta > 0 else "Sin ventas en 12 meses (¿descontinuado?)",
            "variantes_de_nombre": int(g["producto_original"].nunique()),
        })
    productos = pd.DataFrame(filas)
    productos = productos.sort_values(["categoria", "producto_base"]).reset_index(drop=True)
    # código estable por categoría y orden alfabético: FOL-001, QUI-001...
    productos["codigo_producto"] = (
        productos["categoria"].map(PREFIJO_CATEGORIA) + "-" + (productos.groupby("categoria").cumcount() + 1).astype(str).str.zfill(3)
    )
    productos["categoria_sistema"] = productos["categoria"].map(NOMBRE_CATEGORIA)

    # --- por confirmar con el gerente ----------------------------------------------
    notas: list[dict] = []
    por_revisar = df[df["por_revisar"]].drop_duplicates("producto_norm")
    for _, r in por_revisar.iterrows():
        notas.append({"producto_base": r["base"], "tipo": "Nombre nuevo no revisado",
                      "detalle": f"'{r['producto_original']}' no estaba en el mapa de nombres; se interpretó como base '{r['base']}', presentación '{r['presentacion']}'. ¿Es correcto?"})
    for _, r in productos.iterrows():
        if "granel" in r["presentaciones"]:
            notas.append({"producto_base": r["producto_base"], "tipo": "Venta a granel",
                          "detalle": "Una presentación se vende por KG a granel: sus unidades son kilos, no envases, y no se pueden sumar tal cual con sacos o frascos."})
        if r["estado_sugerido"].startswith("Sin ventas"):
            notas.append({"producto_base": r["producto_base"], "tipo": "Posible descontinuado",
                          "detalle": f"Última venta {r['ultimo_mes']}. ¿Sigue en catálogo?"})
    if LISTADO_ANTERIOR.exists():
        previo = pd.read_excel(LISTADO_ANTERIOR, sheet_name="Productos")
        previo = previo[previo["nota_revision"].notna() & previo["producto"].isin(productos["producto_base"])]
        for _, r in previo.iterrows():
            notas.append({"producto_base": r["producto"], "tipo": "Criterio propio de agrupación", "detalle": str(r["nota_revision"])})
    por_confirmar = pd.DataFrame(notas, columns=["producto_base", "tipo", "detalle"]).sort_values(["tipo", "producto_base"]).reset_index(drop=True)

    nombres = (
        df.groupby("base")["producto_original"].agg(lambda s: " | ".join(sorted(s.unique()))).rename("nombres_escritos_en_el_excel").reset_index()
        .rename(columns={"base": "producto_base"})
    )

    columnas = ["codigo_producto", "producto_base", "categoria_sistema", "n_presentaciones", "presentaciones", "unidades_ult_12m",
                "soles_ult_12m", "meses_con_venta_ult_12m", "precio_referencia", "primer_mes", "ultimo_mes", "estado_sugerido", "variantes_de_nombre"]
    return {"productos": productos[columnas], "presentaciones": presentaciones, "nombres": nombres, "por_confirmar": por_confirmar}


def exportar_excel(hojas: dict[str, pd.DataFrame], ruta: Path) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    titulos = {"productos": "Productos base", "presentaciones": "Presentaciones", "nombres": "Nombres en el Excel", "por_confirmar": "Por confirmar"}
    with pd.ExcelWriter(ruta, engine="openpyxl") as escritor:
        for clave, hoja in hojas.items():
            hoja.to_excel(escritor, sheet_name=titulos[clave], index=False)
            ws = escritor.sheets[titulos[clave]]
            for celda in ws[1]:
                celda.font = Font(bold=True, color="FFFFFF")
                celda.fill = PatternFill("solid", fgColor="1F5C3B")
                celda.alignment = Alignment(wrap_text=True, vertical="center")
            for i, col in enumerate(hoja.columns, start=1):
                ancho = min(max(len(str(col)), int(hoja[col].astype(str).str.len().quantile(0.9)) if len(hoja) else 10) + 2, 60)
                ws.column_dimensions[get_column_letter(i)].width = ancho
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions


def main() -> None:
    SALIDA.mkdir(exist_ok=True)
    df, informe = cargar_ventas_limpias(anio_desde=2023)
    hojas = construir_catalogo(df)
    ruta = SALIDA / "catalogo_base_pimentel.xlsx"
    exportar_excel(hojas, ruta)
    p = hojas["productos"]
    print(f"Productos base: {len(p)} ({p['categoria_sistema'].value_counts().to_dict()})")
    print(f"Presentaciones: {len(hojas['presentaciones'])} | por confirmar: {len(hojas['por_confirmar'])}")
    print(f"Con ventas en los últimos 12 meses: {(p['meses_con_venta_ult_12m'] > 0).sum()}")
    print(f"Excel de revisión: {ruta}")


if __name__ == "__main__":
    main()
