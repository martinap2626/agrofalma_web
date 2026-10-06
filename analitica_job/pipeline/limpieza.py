"""
Carga y limpieza de las ventas de la Sede Pimentel (Excel 2023-2026) hasta
dejar un conjunto de líneas de venta con producto BASE, presentación y
categoría, listo para el catálogo y para el modelo.

Qué corrige respecto de leer el Excel "tal cual":
  - hojas con números de comprobante (000xxxx), meses o totales escritos en la
    columna de producto (la hoja de julio 2026 de JORGE traía una fila "JULIO"
    de 3 508 unidades y varios comprobantes: inflaba julio de ~580 a 7 600);
  - alimento animal (bloque 4 y categoría "COMIDA BALANCEADA"), fuera del alcance;
  - nombres distintos de un mismo producto/presentación (se unifican con el
    mapa revisado `referencia/mapa_producto_base.csv`; los nombres que no están
    en el mapa se interpretan con reglas y quedan marcados para revisión).

Las unidades siguen la definición de trabajo ya adoptada: unidades vendidas =
contado + crédito (columnas CONTADO/CANTIDAD); pendiente de confirmar con el
gerente el significado exacto de CONTADO/CREDITO.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from analitica_job.pimentel.cargar_pimentel import cargar, norm

RAIZ_JOB = Path(__file__).resolve().parent.parent
MAPA_PRODUCTOS = RAIZ_JOB / "referencia" / "mapa_producto_base.csv"

# último mes con ventas reales. Los meses posteriores de 2026 en el Excel son una
# plantilla copiada de 2025 (ver memoria del proyecto): se suben cuando haya datos reales
ULTIMO_ANIO_REAL, ULTIMO_MES_REAL = 2026, 8

CATEGORIA_POR_BLOQUE = {1: "QUIMICOS", 2: "FOLIARES", 3: "FERTILIZANTES", 4: "COMIDA BALANCEADA"}
CATEGORIAS_EXCLUIDAS = {"COMIDA BALANCEADA"}

_MESES_Y_TOTALES = {
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SETIEMBRE", "SEPTIEMBRE",
    "OCTUBRE", "NOVIEMBRE", "DICIEMBRE", "TOTAL", "SUBTOTAL", "TOTALES",
}
SIN_PRESENTACION = "sin presentación indicada"

# "250 ML", "X 500", "1 L", "50 KG", "X GALON"... al final del nombre
_PATRON_PRESENTACION = re.compile(
    r"\s+(?:X\s*)?(?P<num>\d+(?:[.,]\d+)?)\s*(?P<uni>ML|GRS|GR|G|KGS|KG|LTS|LT|L)\.?$|\s+(?:X\s*)?(?P<galon>GALON|GALÓN|4L)$|\s+(?:X\s*)?(?P<litro>L|LT|LITRO)$"
)


# True si el "nombre de producto" en realidad es un comprobante, un mes o un total
def es_basura(nombre_norm: str) -> bool:
    return bool(re.fullmatch(r"[0-9 .\-/]+", nombre_norm)) or nombre_norm in _MESES_Y_TOTALES


# interpreta un nombre que no está en el mapa: (base, presentación). Es una regla simple
# y por eso queda marcado "por revisar" en el catálogo
def interpretar_nombre(nombre_norm: str) -> tuple[str, str]:
    m = _PATRON_PRESENTACION.search(nombre_norm)
    if not m:
        return nombre_norm, SIN_PRESENTACION
    base = nombre_norm[: m.start()].strip()
    if m.group("galon"):
        return base, "4 L (galón)"
    if m.group("litro"):
        return base, "1 L"
    uni = m.group("uni")
    uni = {"GRS": "GR", "G": "GR", "KGS": "KG", "LT": "L", "LTS": "L"}.get(uni, uni)
    return base, f"{m.group('num').replace(',', '.')} {uni}"


def cargar_mapa() -> pd.DataFrame:
    return pd.read_csv(MAPA_PRODUCTOS).rename(columns={"n": "producto_norm"})


def cargar_ventas_limpias(anio_desde: int = 2023, verbose: bool = False) -> tuple[pd.DataFrame, dict]:
    """
    Devuelve (líneas de venta limpias, informe de calidad). Una fila por línea de
    venta mensual: anio, mes, producto_original, producto_norm, base, presentacion,
    categoria, unidades, precio, soles, bloque, hoja, por_revisar.
    """
    crudo = cargar()
    informe: dict = {"filas_crudas": int(len(crudo))}

    crudo["es_basura"] = crudo["producto_norm"].map(es_basura)
    informe["filas_descartadas_comprobante_mes_total"] = int(crudo["es_basura"].sum())
    informe["unidades_descartadas_comprobante_mes_total"] = round(float(crudo.loc[crudo["es_basura"], "cantidad"].sum()), 1)
    df = crudo[~crudo["es_basura"]].copy()

    # fuera del periodo con ventas reales (hojas plantilla de sep-nov 2026) y antes del año pedido
    idx = df["anio"] * 12 + df["mes"]
    df = df[(idx <= ULTIMO_ANIO_REAL * 12 + ULTIMO_MES_REAL) & (df["anio"] >= anio_desde)].copy()

    # solo unidades positivas de CONTADO / CANTIDAD (los créditos negativos eran ajustes: ver memoria del proyecto)
    df = df[(df["cantidad"] > 0) & (df["tipo"].isin(["CONTADO", "CANTIDAD", "CREDITO"]))].copy()

    mapa = cargar_mapa()
    df = df.merge(mapa, on="producto_norm", how="left")
    sin_mapa = df["base"].isna()
    df["por_revisar"] = sin_mapa
    interpretado = df.loc[sin_mapa, "producto_norm"].map(interpretar_nombre)
    df.loc[sin_mapa, "base"] = interpretado.map(lambda t: t[0])
    df.loc[sin_mapa, "presentacion"] = interpretado.map(lambda t: t[1])
    df.loc[sin_mapa, "categoria_base"] = df.loc[sin_mapa, "bloque"].map(CATEGORIA_POR_BLOQUE)
    informe["nombres_no_mapeados"] = sorted(df.loc[sin_mapa, "producto_norm"].unique().tolist())

    excluidas = df["categoria_base"].isin(CATEGORIAS_EXCLUIDAS) | (df["bloque"] == 4)
    informe["filas_alimento_animal_excluidas"] = int(excluidas.sum())
    df = df[~excluidas].copy()

    df = df.rename(columns={"categoria_base": "categoria", "producto": "producto_original", "cantidad": "unidades"})
    df["soles"] = df["unidades"] * df["precio"].clip(lower=0)

    columnas = ["anio", "mes", "producto_original", "producto_norm", "base", "presentacion", "categoria",
                "unidades", "precio", "soles", "bloque", "hoja", "por_revisar"]
    df = df[columnas].reset_index(drop=True)

    informe["filas_limpias"] = int(len(df))
    informe["productos_base"] = int(df["base"].nunique())
    informe["periodo"] = f"{int(df['anio'].min())}-{int(df.loc[df['anio'] == df['anio'].min(), 'mes'].min()):02d} a {ULTIMO_ANIO_REAL}-{ULTIMO_MES_REAL:02d}"
    informe["alertas"] = diagnostico_calidad(df)
    if verbose:
        for k, v in informe.items():
            print(f"{k}: {v}")
    return df, informe


# ---------------------------------------------------------------------------
# Diagnóstico de calidad: ¿los años se parecen demasiado para ser ventas reales?
# ---------------------------------------------------------------------------
def diagnostico_calidad(df: pd.DataFrame) -> list[dict]:
    """
    Compara años consecutivos producto por producto y mes por mes, solo con pares
    donde alguno de los dos valores es >= 10 (con 1, 2 o 3 unidades coincidir es
    normal). Se contrasta contra lo que coincidiría por pura casualidad (mismos
    valores con los productos barajados). Si los años coinciden varias veces más de
    lo esperable, es señal de que un año pudo armarse copiando parte del anterior.
    No prueba nada por sí solo: es una alerta para confirmar con el gerente.
    """
    rng = np.random.default_rng(2026)
    por_pm = df.groupby(["anio", "mes", "base"])["unidades"].sum().unstack(0)
    alertas = []
    for a, b in zip(por_pm.columns[:-1], por_pm.columns[1:]):
        comun = por_pm[[a, b]].dropna()
        comun = comun[(comun[a] >= 10) | (comun[b] >= 10)]
        if len(comun) < 30:
            continue
        iguales = float((comun[a] == comun[b]).mean() * 100)
        azar = []
        for _ in range(200):
            barajado = comun[b].to_numpy().copy()
            rng.shuffle(barajado)
            azar.append((comun[a].to_numpy() == barajado).mean() * 100)
        esperado = float(np.mean(azar))
        alertas.append({
            "años": f"{a} vs {b}", "pares_comparados": int(len(comun)),
            "pct_valores_identicos": round(iguales, 1), "esperado_por_azar_pct": round(esperado, 1),
            "veces_lo_esperado": round(iguales / esperado, 1) if esperado else None,
            "correlacion_mensual": round(float(comun[a].corr(comun[b])), 3),
            "atipico": bool(iguales > max(2 * esperado, esperado + 5)),
        })
    return alertas


# unidades por mes y producto base (panel completo con ceros), de enero del primer año al último mes real
def panel_mensual(df: pd.DataFrame) -> pd.DataFrame:
    anio0 = int(df["anio"].min())
    meses = pd.period_range(f"{anio0}-01", f"{ULTIMO_ANIO_REAL}-{ULTIMO_MES_REAL:02d}", freq="M")
    # a cada producto base se le calcula primero cuándo empezó a venderse (no se predice antes de existir)
    unidades = df.groupby(["base", "anio", "mes"])["unidades"].sum().reset_index()
    unidades["periodo"] = pd.PeriodIndex.from_fields(year=unidades["anio"], month=unidades["mes"], freq="M")
    ancho = unidades.pivot_table(index="base", columns="periodo", values="unidades", aggfunc="sum").reindex(columns=meses)
    categoria = df.groupby("base")["categoria"].agg(lambda s: s.mode().iat[0])
    ancho = ancho.fillna(0.0)
    largo = ancho.stack().rename("unidades").reset_index()
    largo.columns = ["base", "periodo", "unidades"]
    largo["categoria"] = largo["base"].map(categoria)
    largo["mes_idx"] = (largo["periodo"].dt.year - anio0) * 12 + largo["periodo"].dt.month
    return largo.sort_values(["base", "periodo"]).reset_index(drop=True)
