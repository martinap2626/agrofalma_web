"""
Pipeline de analítica de punta a punta, un solo comando:

    datos de Pimentel (Excel) -> limpieza -> producto base -> panel mensual ->
    segmentación ABC -> validación de métodos (origen móvil) -> elección por
    clase -> pronóstico del mes siguiente -> confiabilidad y tendencia

`ejecutar()` no toca la base de datos: devuelve un `ResultadoAnalitica` que se
puede guardar en MySQL (persistir.py) y exportar a Excel (exportar_excel).

Uso:  python -m analitica_job.pipeline.ejecutar
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from analitica_job.pipeline import campanas, clasificacion_abc, limpieza, modelos

SALIDA = Path(__file__).resolve().parent.parent / "datos_procesados"

# ventana de entrenamiento por defecto: desde 2023. El gerente confirmó que los Excel 2023-2024
# son ventas reales (el diagnóstico de calidad queda como información en el informe). Con más
# historia el aprendizaje automático mejora mucho (WAPE clase A: 65% -> 38%) y se iguala al
# método estacional; se puede cambiar con --anio-desde
ANIO_DESDE_POR_DEFECTO = 2023
ORIGENES_VALIDACION = 8


@dataclass
class ResultadoAnalitica:
    anio_desde: int
    informe_calidad: dict
    prediccion: pd.DataFrame          # una fila por producto base
    abc: pd.DataFrame
    metricas_metodo: pd.DataFrame     # todos los productos juntos
    metricas_clase: pd.DataFrame      # por clase ABC (toda la validación)
    metodo_por_clase: dict[str, str]
    validacion: pd.DataFrame          # detalle mes a mes del método elegido
    reparto_presentaciones: pd.DataFrame
    plan_campana: pd.DataFrame = field(default_factory=pd.DataFrame)   # una fila por producto y campaña (en curso / próxima)
    meta_campana: dict = field(default_factory=dict)
    segundos: float = 0.0
    extra: dict = field(default_factory=dict)


# confiabilidad del pronóstico de un producto, en lenguaje de negocio (no se muestra WAPE al usuario)
def confiabilidad(wape_producto: float | None, meses_con_venta_12m: int) -> str:
    if wape_producto is None or meses_con_venta_12m < 4:
        return "Baja"  # casi no hay historia o no hubo ventas en la validación: no se puede confiar
    if wape_producto <= 35 and meses_con_venta_12m >= 8:
        return "Alta"
    if wape_producto <= 70:
        return "Media"
    return "Baja"


def _reparto_por_presentacion(df: pd.DataFrame, ultimo_anio: int, ultimo_mes: int) -> pd.DataFrame:
    """Qué parte de las unidades de cada producto base se vendió en cada presentación (últimos 12 meses)."""
    fin = ultimo_anio * 12 + ultimo_mes
    v = df[(df["anio"] * 12 + df["mes"] > fin - 12)]
    r = v.groupby(["base", "presentacion"])["unidades"].sum().reset_index()
    r["participacion_pct"] = (r["unidades"] / r.groupby("base")["unidades"].transform("sum") * 100).round(1)
    return r.sort_values(["base", "participacion_pct"], ascending=[True, False]).reset_index(drop=True)


def incorporar_ventas_sistema(df: pd.DataFrame, ventas: pd.DataFrame | None, ultimo: tuple[int, int]) -> tuple[pd.DataFrame, tuple[int, int], int]:
    """
    Amplía el historial de los Excel con las ventas registradas en la propia plataforma después del último mes de los Excel.

    `ventas` trae una fila por producto base y mes COMPLETO (columnas base, anio, mes, categoria, unidades, soles). Solo se aceptan meses
    consecutivos a partir del mes siguiente al último de los Excel: un hueco cortaría la serie. Devuelve (datos ampliados,
    (año, mes) final, cantidad de meses incorporados).
    """
    if ventas is None or ventas.empty:
        return df, ultimo, 0
    v = ventas.copy()
    v["idx"] = v["anio"] * 12 + v["mes"]
    siguiente = ultimo[0] * 12 + ultimo[1] + 1
    meses = sorted(v.loc[v["idx"] >= siguiente, "idx"].unique())
    validos = []
    for m in meses:  # meses consecutivos desde el siguiente al último de los Excel
        if m == siguiente + len(validos):
            validos.append(m)
        else:
            break
    if not validos:
        return df, ultimo, 0
    v = v[v["idx"].isin(validos)]
    nuevas = pd.DataFrame({
        "anio": v["anio"].astype(int), "mes": v["mes"].astype(int), "producto_original": v["base"], "producto_norm": v["base"], "base": v["base"],
        "presentacion": "sin presentación indicada", "categoria": v["categoria"], "unidades": v["unidades"].astype(float),
        "precio": (v["soles"] / v["unidades"]).where(v["unidades"] > 0, 0.0), "soles": v["soles"].astype(float), "bloque": 0, "hoja": "sistema",
        "por_revisar": False,
    })
    fin = validos[-1] - 1
    return pd.concat([df, nuevas], ignore_index=True), (fin // 12, fin % 12 + 1), len(validos)


def _aplicar_ajustes(panel: pd.DataFrame, ajustes: pd.DataFrame | None) -> pd.DataFrame:
    """
    Suma al panel mensual la demanda no atendida registrada por el gerente (quiebres de stock): lo que no se vendió
    por falta de producto también era demanda. `ajustes` tiene columnas base, anio, mes, unidades. Solo toca
    productos y meses que ya existen en el panel (no inventa historia).
    """
    if ajustes is None or ajustes.empty:
        return panel
    panel = panel.copy()
    ajustes = ajustes.groupby(["base", "anio", "mes"], as_index=False)["unidades"].sum()
    for r in ajustes.itertuples(index=False):
        marca = (panel["base"] == r.base) & (panel["periodo"].dt.year == r.anio) & (panel["periodo"].dt.month == r.mes)
        panel.loc[marca, "unidades"] += float(r.unidades)
    return panel


def ejecutar(anio_desde: int = ANIO_DESDE_POR_DEFECTO, n_origenes: int = ORIGENES_VALIDACION, verbose: bool = False,
             ajustes: pd.DataFrame | None = None, ventas_sistema: pd.DataFrame | None = None) -> ResultadoAnalitica:
    """Corre el pipeline. `ventas_sistema` (opcional) son las ventas mensuales de la plataforma posteriores a los Excel."""
    original = (limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL)
    try:
        return _ejecutar(anio_desde, n_origenes, verbose, ajustes, ventas_sistema)
    finally:  # el último mes "real" se amplía solo durante esta corrida
        limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL = original


def _ejecutar(anio_desde, n_origenes, verbose, ajustes, ventas_sistema) -> ResultadoAnalitica:
    t0 = time.perf_counter()
    log = print if verbose else (lambda *a, **k: None)

    log(f"1/6 Cargando y limpiando ventas de Pimentel (desde {anio_desde}) ...")
    df, informe = limpieza.cargar_ventas_limpias(anio_desde=anio_desde)
    # el diagnóstico de calidad siempre se hace sobre toda la historia disponible, aunque se entrene con menos
    informe["alertas"] = limpieza.cargar_ventas_limpias(anio_desde=2023)[1]["alertas"]
    df, (anio_fin, mes_fin), n_meses_sistema = incorporar_ventas_sistema(
        df, ventas_sistema, (limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL))
    if n_meses_sistema:
        limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL = anio_fin, mes_fin
        informe["periodo"] = informe["periodo"].split(" a ")[0] + f" a {anio_fin}-{mes_fin:02d}"
    informe["meses_desde_sistema"] = n_meses_sistema

    log("2/6 Armando el panel mensual por producto base ...")
    serie = modelos.construir_serie(_aplicar_ajustes(limpieza.panel_mensual(df), ajustes))

    log("3/6 Segmentación ABC (últimos 12 meses) ...")
    abc = clasificacion_abc.segmentar(df, limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL, serie.productos)
    clases = abc.set_index("base")["clase_abc"]

    log(f"4/6 Validando {len(modelos.METODOS)} métodos en {n_origenes} meses de prueba ...")
    validacion = modelos.validar(serie, n_origenes)
    met_clase = modelos.metricas(validacion, serie, clases)
    met_todos = modelos.metricas(validacion, serie)
    # los últimos 3 meses de prueba pesan igual que toda la validación al elegir el método
    ultimos = sorted(validacion["periodo"].unique())[-3:]
    met_reciente = modelos.metricas(validacion[validacion["periodo"].isin(ultimos)], serie, clases)
    elegido = modelos.elegir_metodo_por_clase(met_clase, met_reciente)

    log(f"5/6 Pronóstico del mes siguiente con el método elegido por clase: {elegido}")
    pred = modelos.pronosticar_siguiente(serie, elegido, clases)

    log("6/6 Confiabilidad y tendencia ...")
    # error del método ELEGIDO para cada producto, en los meses de prueba
    elegido_por_producto = pred.set_index("producto")["metodo"]
    val_sel = validacion[validacion["metodo"] == validacion["producto"].map(elegido_por_producto)]
    err = val_sel.groupby("producto").apply(
        lambda g: (g["real"] - g["pred"]).abs().sum() / g["real"].sum() * 100 if g["real"].sum() > 0 else None, include_groups=False
    )
    # error absoluto medio (unidades por mes) del método elegido: base del margen de seguridad en las recomendaciones
    mae = val_sel.groupby("producto").apply(lambda g: (g["real"] - g["pred"]).abs().mean(), include_groups=False)
    fin = limpieza.ULTIMO_ANIO_REAL * 12 + limpieza.ULTIMO_MES_REAL
    ult12 = df[(df["anio"] * 12 + df["mes"] > fin - 12)].groupby(["base", "anio", "mes"]).size().groupby("base").size()

    pred["clase_abc"] = pred["producto"].map(clases)
    pred["categoria"] = pred["producto"].map(abc.set_index("base")["categoria"])
    pred["meses_con_venta_12m"] = pred["producto"].map(ult12).fillna(0).astype(int)
    pred["error_validacion_pct"] = pred["producto"].map(err)
    pred["mae_validacion"] = pred["producto"].map(mae)
    pred["confiabilidad"] = [confiabilidad(e if pd.notna(e) else None, m) for e, m in zip(pred["error_validacion_pct"], pred["meses_con_venta_12m"])]
    pred["cantidad_sugerida"] = [math.ceil(round(v, 6)) for v in pred["pronostico"]]
    prev = pred["venta_mes_anterior"]
    tend = ((pred["pronostico"] - prev) / prev * 100).where(prev > 0)
    pred["tendencia_pct"] = tend.clip(-999, 999).round(1)

    log("Plan de campaña (grande: ene-jun; chica: jul-dic) ...")
    plan, meta_campana = campanas.plan_campana(df, clases, serie.productos, limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL)

    return ResultadoAnalitica(
        anio_desde=anio_desde, informe_calidad=informe, prediccion=pred, abc=abc, metricas_metodo=met_todos,
        metricas_clase=met_clase, metodo_por_clase=elegido, validacion=val_sel,
        reparto_presentaciones=_reparto_por_presentacion(df, limpieza.ULTIMO_ANIO_REAL, limpieza.ULTIMO_MES_REAL),
        plan_campana=plan, meta_campana=meta_campana,
        segundos=round(time.perf_counter() - t0, 1),
        extra={"ajustes": ajustes if ajustes is not None else pd.DataFrame()},
    )


def exportar_excel(res: ResultadoAnalitica, ruta: Path | None = None) -> Path:
    """Excel para revisar con el gerente / asesor: pronóstico, ABC, métricas, calidad de datos."""
    SALIDA.mkdir(exist_ok=True)
    ruta = ruta or SALIDA / "resultados_analitica_pimentel.xlsx"
    p = res.prediccion.sort_values("pronostico", ascending=False)[[
        "producto", "categoria", "clase_abc", "periodo_objetivo", "cantidad_sugerida", "venta_mes_anterior", "tendencia_pct",
        "confiabilidad", "metodo", "meses_con_venta_12m", "error_validacion_pct"]]
    calidad = pd.DataFrame(res.informe_calidad["alertas"])
    resumen = pd.DataFrame({"dato": [
        "Productos base", "Periodo de datos usado", "Entrenado desde", "Filas descartadas (comprobante/mes/total)",
        "Unidades descartadas por ese motivo", "Método elegido por clase", "Duración (s)"],
        "valor": [res.informe_calidad["productos_base"], res.informe_calidad["periodo"], res.anio_desde,
                  res.informe_calidad["filas_descartadas_comprobante_mes_total"],
                  res.informe_calidad["unidades_descartadas_comprobante_mes_total"], str(res.metodo_por_clase), res.segundos]})
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        resumen.to_excel(w, sheet_name="Resumen", index=False)
        p.to_excel(w, sheet_name="Pronóstico", index=False)
        res.abc.to_excel(w, sheet_name="ABC", index=False)
        res.metricas_clase.round(2).to_excel(w, sheet_name="Métricas por clase", index=False)
        res.metricas_metodo.round(2).to_excel(w, sheet_name="Métricas por método", index=False)
        calidad.to_excel(w, sheet_name="Calidad de datos", index=False)
        res.reparto_presentaciones.to_excel(w, sheet_name="Reparto por presentación", index=False)
        if len(res.plan_campana):
            res.plan_campana.round(1).to_excel(w, sheet_name="Plan de campaña", index=False)
        res.validacion.round(2).to_excel(w, sheet_name="Validación detalle", index=False)
    return ruta


def main() -> None:
    res = ejecutar(verbose=True)
    ruta = exportar_excel(res)
    print(f"\nListo en {res.segundos} s. Excel: {ruta}")
    print(res.metricas_clase.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
