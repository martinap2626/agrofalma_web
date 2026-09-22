"""
Job offline de preparación de datos para el componente analítico (HU-09/10,
Sprint 3). No corre dentro de la app Flask -- app/analitica/routes.py solo
lee tablas ya precalculadas (ver esa carpeta); este script (y los que le
sigan para entrenamiento) se ejecutan a mano o por cron, fuera de la app.

Toma el Excel combinado de ventas 2025-2026 (una fila por transacción) y
produce un dataset "panel" (una fila por producto-mes), separado en
entrenamiento y prueba fuera de tiempo (out-of-time).

Uso:
    python -m analitica_job.preparar_datos
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# El dataset de negocio vive fuera del repo de código (Dataset/ es data,
# no código -- no se versiona en git).
RAIZ_TESIS = Path(__file__).resolve().parents[3]
EXCEL_COMBINADO = RAIZ_TESIS / "Dataset" / "datasets combinados" / "ventas_2025_2026_combinado.xlsx"

CARPETA_SALIDA = Path(__file__).resolve().parent / "datos_procesados"

__all__ = [
    "CARPETA_SALIDA",
    "ULTIMO_MES_REAL_2026",
    "ULTIMO_MES_ENTRENAMIENTO_2026",
    "cargar_ventas",
    "filtrar_categoria_excluida",
    "filtrar_meses_no_confiables",
    "calcular_unidades_vendidas",
]

# Fuera del alcance del modelo agrícola (100% alimento animal). El
# combinado actual ya no la trae, pero se deja el filtro por si se
# regenera sin este paso.
CATEGORIA_EXCLUIDA = "COMIDA BALANCEADA"

# Último mes de 2026 con ventas reales confirmadas (no plantilla copiada
# de 2025 -- ver hallazgo: sept/oct/nov 2026 en el combinado original son
# casi idénticas a sept/oct/nov 2025, fila por fila). Subir este número
# conforme se reemplacen esas hojas por ventas reales del mes.
ULTIMO_MES_REAL_2026 = 8

# Corte fijo de entrenamiento: enero 2025 - junio 2026. Julio en adelante
# de 2026 queda como prueba (holdout real, fuera de tiempo), y va
# creciendo con el tiempo sin mover el corte de entrenamiento.
ULTIMO_MES_ENTRENAMIENTO_2026 = 6


def cargar_ventas() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_COMBINADO)
    df = df.dropna(subset=["anio", "mes_numero", "producto_normalizado"])
    df["anio"] = df["anio"].astype(int)
    df["mes_numero"] = df["mes_numero"].astype(int)
    return df


def filtrar_categoria_excluida(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["categoria"] != CATEGORIA_EXCLUIDA].copy()


def filtrar_meses_no_confiables(df: pd.DataFrame) -> pd.DataFrame:
    mask_valida = (df["anio"] != 2026) | (df["mes_numero"] <= ULTIMO_MES_REAL_2026)
    return df[mask_valida].copy()


# El propio informe de limpieza del dataset (Informe_Limpieza_Normalizacion_Ventas2026.docx,
# seccion "Ambiguedades que no se resolvieron a proposito") deja explicito que el significado
# real de cantidad_credito en sept-nov (patron negativo/positivo) NO esta confirmado con el
# gerente -- se guardo tal cual, marcado en credito_signo_hoja. Sumar contado+credito a ciegas
# en esas filas cancela ventas reales a cero (ej. contado=22, credito=-22 -> 0). Mientras no se
# confirme el significado real, se usa el mayor de los dos valores absolutos (no se pierde
# evidencia de venta, pero tampoco se asume una interpretacion de negocio no confirmada).
def calcular_unidades_vendidas(df: pd.DataFrame) -> pd.Series:
    contado = df["cantidad_contado"].fillna(0)
    credito = df["cantidad_credito"].fillna(0)
    es_ambiguo = df["credito_signo_hoja"] == "mixto_negativo_positivo"

    suma_normal = contado + credito
    maximo_seguro = pd.concat([contado, credito.abs()], axis=1).max(axis=1)
    return suma_normal.where(~es_ambiguo, maximo_seguro)


def agregar_panel_mensual(df: pd.DataFrame) -> pd.DataFrame:
    # "todo_entregado" (salidas internas) queda fuera a proposito, no es una venta a cliente.
    df = df.copy()
    df["unidades_vendidas"] = calcular_unidades_vendidas(df)

    panel = (
        df.groupby(
            ["producto_normalizado", "categoria", "anio", "mes_numero", "mes", "campana"],
            as_index=False,
        )
        .agg(
            unidades_vendidas=("unidades_vendidas", "sum"),
            ventas_soles=("total_soles_calculado", "sum"),
            n_transacciones=("unidades_vendidas", "count"),
        )
        .sort_values(["producto_normalizado", "anio", "mes_numero"])
        .reset_index(drop=True)
    )
    return panel


def dividir_train_test(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    es_train = (panel["anio"] == 2025) | (
        (panel["anio"] == 2026) & (panel["mes_numero"] <= ULTIMO_MES_ENTRENAMIENTO_2026)
    )
    return panel[es_train].copy(), panel[~es_train].copy()


def main() -> None:
    CARPETA_SALIDA.mkdir(exist_ok=True)

    df = cargar_ventas()
    print(f"Filas crudas: {len(df)}")

    df = filtrar_categoria_excluida(df)
    df = filtrar_meses_no_confiables(df)
    print(f"Filas tras filtros (sin comida balanceada, sin meses plantilla): {len(df)}")

    panel = agregar_panel_mensual(df)
    n_meses = panel[["anio", "mes_numero"]].drop_duplicates().shape[0]
    print(f"Panel producto-mes: {len(panel)} filas, {panel['producto_normalizado'].nunique()} productos, {n_meses} meses")

    train, test = dividir_train_test(panel)
    print(f"Entrenamiento: {len(train)} filas")
    print(f"Prueba (holdout real): {len(test)} filas")
    if not test.empty:
        meses_test = sorted(test[["anio", "mes_numero"]].drop_duplicates().itertuples(index=False, name=None))
        print(f"Meses de prueba: {meses_test}")

    train.to_csv(CARPETA_SALIDA / "panel_entrenamiento.csv", index=False)
    test.to_csv(CARPETA_SALIDA / "panel_prueba.csv", index=False)
    print(f"Guardado en {CARPETA_SALIDA}")


if __name__ == "__main__":
    main()
