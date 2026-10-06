"""
Prueba exploratoria (rama test02): que tan lejos del 15% de MAPE queda el
candidato oficial (dos etapas, hito 80%) si se cambia la FORMULA (sMAPE en
vez de MAPE) o el NIVEL DE AGREGACION (por categoria, o acumulado del
periodo completo de prueba) en vez de por producto-mes individual.

No se toca ningun dato de entrada -- mismas predicciones del mismo modelo
candidato ya entrenado, solo se recalculan/reagregan las metricas.

Uso:
    python -m analitica_job.explorar_metricas_alternativas
"""

from __future__ import annotations

import joblib
import pandas as pd

from analitica_job.entrenar_modelo import COLUMNA_OBJETIVO, COLUMNAS_CATEGORICAS, COLUMNAS_NUMERICAS, cargar_datasets
from analitica_job.metricas import mape, smape
from analitica_job.preparar_datos import CARPETA_SALIDA

CARPETA_MODELOS = CARPETA_SALIDA.parent / "modelos"


def cargar_candidato():
    ruta_dos_etapas = CARPETA_MODELOS / "modelo_demanda_candidato_dos_etapas.joblib"
    ruta_una_etapa = CARPETA_MODELOS / "modelo_demanda_candidato.joblib"
    if ruta_dos_etapas.exists():
        return joblib.load(ruta_dos_etapas), "dos etapas"
    return joblib.load(ruta_una_etapa), "una etapa"


def reportar(titulo: str, y_real, y_pred) -> None:
    valor_mape = mape(y_real, y_pred)
    valor_smape = smape(y_real, y_pred)
    mape_txt = f"{valor_mape:.1f}%" if valor_mape is not None else "N/D (todo cero)"
    smape_txt = f"{valor_smape:.1f}%" if valor_smape is not None else "N/D (todo cero)"
    print(f"{titulo:55s} MAPE={mape_txt:>10s}   sMAPE={smape_txt:>10s}")


def main() -> None:
    _, test = cargar_datasets()
    modelo, tipo = cargar_candidato()
    print(f"Candidato cargado: {tipo}\n")

    X_test = test[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS]
    test = test.assign(prediccion=modelo.predict(X_test))

    segmentacion = pd.read_csv(CARPETA_SALIDA / "segmentacion_abc.csv")
    test = test.merge(segmentacion[["producto_normalizado", "clase_abc"]], on="producto_normalizado", how="left")

    print("=== Nivel actual: producto-mes individual (lo que ya reportabas) ===")
    reportar("Todo el catalogo, producto-mes", test[COLUMNA_OBJETIVO], test["prediccion"])
    solo_a = test[test["clase_abc"] == "A"]
    reportar("Solo clase A, producto-mes", solo_a[COLUMNA_OBJETIVO], solo_a["prediccion"])

    print("\n=== Opcion 2: agregado por categoria-mes (suma de productos) ===")
    agregado_categoria = (
        test.groupby(["categoria", "anio", "mes_numero"], as_index=False)
        .agg(real=(COLUMNA_OBJETIVO, "sum"), predicho=("prediccion", "sum"))
    )
    reportar("Todo el catalogo, categoria-mes", agregado_categoria["real"], agregado_categoria["predicho"])

    print("\n=== Opcion 3 (bonus): acumulado del periodo de prueba completo, por producto ===")
    acumulado_producto = (
        test.groupby(["producto_normalizado", "clase_abc"], as_index=False)
        .agg(real=(COLUMNA_OBJETIVO, "sum"), predicho=("prediccion", "sum"))
    )
    reportar("Todo el catalogo, producto (jul+ago 2026 sumado)", acumulado_producto["real"], acumulado_producto["predicho"])
    solo_a_acum = acumulado_producto[acumulado_producto["clase_abc"] == "A"]
    reportar("Solo clase A, producto (jul+ago 2026 sumado)", solo_a_acum["real"], solo_a_acum["predicho"])

    print("\n=== Detalle por categoria (para ver cuales quedan mas cerca de 15%) ===")
    filas = []
    for categoria, grupo in agregado_categoria.groupby("categoria"):
        filas.append({
            "categoria": categoria,
            "MAPE_%": mape(grupo["real"], grupo["predicho"]),
            "sMAPE_%": smape(grupo["real"], grupo["predicho"]),
        })
    tabla_categorias = pd.DataFrame(filas).sort_values("MAPE_%")
    print(tabla_categorias.to_string(index=False))
    tabla_categorias.to_csv(CARPETA_SALIDA / "mape_por_categoria_agregado.csv", index=False)
    print(f"\nGuardado: {CARPETA_SALIDA / 'mape_por_categoria_agregado.csv'}")


if __name__ == "__main__":
    main()
