"""
Llena las tablas de vista previa (predicciones_demanda_preview,
segmentacion_productos_preview) con resultados reales del job de
analítica, para poder mostrarlos en la interfaz web mientras el catálogo
real de productos no esté disponible (ver
app/models/prediccion_demanda_preview.py y
app/models/segmentacion_producto_preview.py).

Para la predicción, genera el pronóstico del PRÓXIMO mes real (el
siguiente a ULTIMO_MES_REAL_2026) por producto, usando el modelo candidato
ya entrenado y los últimos 3 meses reales de cada producto como rezagos --
no vuelve a mostrar el conjunto de prueba, proyecta hacia adelante.

Requiere haber corrido antes, en este orden:
    python -m analitica_job.construir_features
    python -m analitica_job.entrenar_modelo
    python -m analitica_job.segmentar_abc

Uso:
    python -m analitica_job.cargar_vista_previa_bd
"""

from __future__ import annotations

import math

import joblib
import numpy as np
import pandas as pd

from analitica_job.construir_features import campana_de, indice_mes
from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS
from analitica_job.metricas import escala_naive_por_producto, mase
from analitica_job.preparar_datos import CARPETA_SALIDA, ULTIMO_MES_REAL_2026

# El administrador no conoce el MASE ni deberia tener que aprenderlo -- se
# traduce a un badge de negocio. Umbrales propios del proyecto (no hay una
# escala academica universal para MASE, a diferencia del MAPE/Lewis 1982 --
# ver Documentacion_Avance80.docx, seccion 2): <0.5 el modelo le gana con
# holgura al metodo ingenuo, 0.5-1.0 le gana con margen mas ajustado, >=1.0
# no le gana (no deberia ocurrir con el candidato actual, pero se contempla).
def confiabilidad_desde_mase(valor_mase: float | None) -> str:
    if valor_mase is None:
        return "Sin dato"
    if valor_mase < 0.5:
        return "Alta"
    if valor_mase < 1.0:
        return "Media"
    return "Baja"


def construir_fila_proximo_mes(dataset_completo: pd.DataFrame) -> pd.DataFrame:
    idx_ultimo_real = indice_mes(2026, ULTIMO_MES_REAL_2026)
    idx_objetivo = idx_ultimo_real + 1
    anio_obj = 2025 + (idx_objetivo - 1) // 12
    mes_obj = (idx_objetivo - 1) % 12 + 1

    # lag_1/2/3 = ultimos 3 meses reales de cada producto (en orden, mas reciente al final)
    def lags_producto(grupo):
        valores = grupo.sort_values("mes_idx")["unidades_vendidas"].tolist()
        valores = ([0] * 3 + valores)[-3:]  # asegura al menos 3 valores, rellenando con 0
        return pd.Series({"lag_1": valores[-1], "lag_2": valores[-2], "lag_3": valores[-3]})

    lags = (
        dataset_completo[dataset_completo["mes_idx"] > idx_ultimo_real - 3]
        .groupby("producto_normalizado")
        .apply(lags_producto, include_groups=False)
        .reset_index()
    )
    categorias = dataset_completo.groupby("producto_normalizado")["categoria"].last().reset_index()

    # recencia para el mes objetivo: meses consecutivos sin venta al final del
    # historial conocido de cada producto (0 si el ultimo mes real tuvo venta)
    def recencia_producto(grupo):
        meses_sin_venta = 0
        for venta in grupo.sort_values("mes_idx")["unidades_vendidas"]:
            meses_sin_venta = 0 if venta > 0 else meses_sin_venta + 1
        return meses_sin_venta

    recencias = (
        dataset_completo.groupby("producto_normalizado")
        .apply(recencia_producto, include_groups=False)
        .rename("recencia")
        .reset_index()
    )

    fila = lags.merge(categorias, on="producto_normalizado", how="left")
    fila = fila.merge(recencias, on="producto_normalizado", how="left")
    fila["promedio_movil_3"] = fila[["lag_1", "lag_2", "lag_3"]].mean(axis=1)
    fila["mes_numero"] = mes_obj
    fila["campana"] = campana_de(mes_obj)
    fila["periodo_objetivo"] = f"{anio_obj}-{mes_obj:02d}"
    return fila


def calcular_mase_por_clase(test: pd.DataFrame, modelo, escala: pd.Series, segmentacion: pd.DataFrame) -> dict[str, float]:
    cols = COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS
    test_con_pred = test.assign(_pred=np.clip(modelo.predict(test[cols]), 0, None))
    test_con_clase = test_con_pred.merge(
        segmentacion[["producto_normalizado", "clase_abc"]], on="producto_normalizado", how="left"
    )
    test_con_clase["clase_abc"] = test_con_clase["clase_abc"].fillna("Sin clasificar")

    mase_por_clase = {}
    for clase, grupo in test_con_clase.groupby("clase_abc"):
        valor = mase(grupo, "unidades_vendidas", "_pred", escala)
        mase_por_clase[clase] = valor
    return mase_por_clase


def generar_predicciones_proximo_mes(segmentacion: pd.DataFrame) -> pd.DataFrame:
    train = pd.read_csv(CARPETA_SALIDA / "dataset_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "dataset_prueba.csv")
    dataset_completo = pd.concat([train, test], ignore_index=True)

    modelo = joblib.load(CARPETA_SALIDA.parent / "modelos" / "modelo_demanda_candidato.joblib")
    cols = COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS

    # MASE real del candidato actual, calculado por clase ABC sobre el conjunto
    # de prueba (no se lee de un CSV de una comparacion anterior, que podria
    # quedar desactualizada si el candidato cambia -- ver Documentacion_Avance80.docx).
    escala = escala_naive_por_producto(train)
    mase_por_clase = calcular_mase_por_clase(test, modelo, escala, segmentacion)

    fila = construir_fila_proximo_mes(dataset_completo)
    fila = fila.merge(segmentacion[["producto_normalizado", "clase_abc"]], on="producto_normalizado", how="left")
    fila["clase_abc"] = fila["clase_abc"].fillna("Sin clasificar")

    prediccion_cruda = np.clip(modelo.predict(fila[cols]), 0, None)
    fila["cantidad_sugerida"] = [math.ceil(v) for v in prediccion_cruda]
    fila["venta_mes_anterior"] = fila["lag_1"]
    fila["confiabilidad"] = fila["clase_abc"].map(
        lambda c: confiabilidad_desde_mase(mase_por_clase.get(c))
    )

    return fila[[
        "producto_normalizado", "categoria", "clase_abc", "periodo_objetivo",
        "cantidad_sugerida", "venta_mes_anterior", "confiabilidad",
    ]]


def cargar_en_bd(predicciones: pd.DataFrame, segmentacion: pd.DataFrame) -> None:
    from app import create_app
    from app.extensions import db
    from app.models import PrediccionDemandaPreview, SegmentacionProductoPreview

    app = create_app()
    with app.app_context():
        PrediccionDemandaPreview.query.delete()
        for _, fila in predicciones.iterrows():
            venta_anterior = fila["venta_mes_anterior"]
            db.session.add(
                PrediccionDemandaPreview(
                    producto_normalizado=fila["producto_normalizado"],
                    categoria=fila["categoria"],
                    clase_abc=fila["clase_abc"],
                    periodo_objetivo=fila["periodo_objetivo"],
                    cantidad_sugerida=int(fila["cantidad_sugerida"]),
                    venta_mes_anterior=round(float(venta_anterior), 2) if venta_anterior is not None else None,
                    confiabilidad=fila["confiabilidad"],
                )
            )

        SegmentacionProductoPreview.query.delete()
        for _, fila in segmentacion.iterrows():
            db.session.add(
                SegmentacionProductoPreview(
                    producto_normalizado=fila["producto_normalizado"],
                    categoria=fila["categoria"],
                    periodo_inicio=fila["periodo_inicio"][:7],
                    periodo_fin=fila["periodo_fin"][:7],
                    ventas_periodo=round(float(fila["ventas_periodo"]), 2),
                    pct_acumulado=round(float(fila["pct_acumulado"]), 2),
                    clase_abc=fila["clase_abc"],
                )
            )

        db.session.commit()
        print(f"Cargadas {len(predicciones)} predicciones y {len(segmentacion)} segmentaciones en la BD.")


def main() -> None:
    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    predicciones = generar_predicciones_proximo_mes(segmentacion)
    cargar_en_bd(predicciones, segmentacion)


if __name__ == "__main__":
    main()
