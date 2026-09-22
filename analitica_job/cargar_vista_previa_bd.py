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

import joblib
import numpy as np
import pandas as pd

from analitica_job.construir_features import campana_de, indice_mes
from analitica_job.entrenar_modelo import COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS
from analitica_job.preparar_datos import CARPETA_SALIDA, ULTIMO_MES_REAL_2026


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

    fila = lags.merge(categorias, on="producto_normalizado", how="left")
    fila["promedio_movil_3"] = fila[["lag_1", "lag_2", "lag_3"]].mean(axis=1)
    fila["mes_numero"] = mes_obj
    fila["campana"] = campana_de(mes_obj)
    fila["periodo_objetivo"] = f"{anio_obj}-{mes_obj:02d}"
    return fila


def generar_predicciones_proximo_mes() -> pd.DataFrame:
    train = pd.read_csv(CARPETA_SALIDA / "dataset_entrenamiento.csv")
    test = pd.read_csv(CARPETA_SALIDA / "dataset_prueba.csv")
    dataset_completo = pd.concat([train, test], ignore_index=True)

    modelo = joblib.load(CARPETA_SALIDA.parent / "modelos" / "modelo_demanda_candidato.joblib")
    comparacion = pd.read_csv(CARPETA_SALIDA / "comparacion_modelos.csv")
    mape_candidato = comparacion[
        (comparacion["conjunto"] == "prueba") & (comparacion["modelo"] != "Linea base (promedio movil 3 meses)")
    ].sort_values("MAPE_%_solo_meses_con_venta").iloc[0]["MAPE_%_solo_meses_con_venta"]

    fila = construir_fila_proximo_mes(dataset_completo)
    cols = COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS
    fila["demanda_estimada"] = np.clip(modelo.predict(fila[cols]), 0, None)
    fila["mape_modelo"] = mape_candidato

    return fila[["producto_normalizado", "categoria", "periodo_objetivo", "demanda_estimada", "mape_modelo"]]


def cargar_en_bd(predicciones: pd.DataFrame, segmentacion: pd.DataFrame) -> None:
    from app import create_app
    from app.extensions import db
    from app.models import PrediccionDemandaPreview, SegmentacionProductoPreview

    app = create_app()
    with app.app_context():
        PrediccionDemandaPreview.query.delete()
        for _, fila in predicciones.iterrows():
            db.session.add(
                PrediccionDemandaPreview(
                    producto_normalizado=fila["producto_normalizado"],
                    categoria=fila["categoria"],
                    periodo_objetivo=fila["periodo_objetivo"],
                    demanda_estimada=round(float(fila["demanda_estimada"]), 2),
                    mape_modelo=round(float(fila["mape_modelo"]), 2),
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
    predicciones = generar_predicciones_proximo_mes()
    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    cargar_en_bd(predicciones, segmentacion)


if __name__ == "__main__":
    main()
