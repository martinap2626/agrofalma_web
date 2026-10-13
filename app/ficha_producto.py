"""
Ficha de un producto (pantalla «Ficha del producto» de la analítica): junta en una sola vista la venta esperada con su rango
probable, el stock y el riesgo de quedarse sin producto mientras llega la reposición, las ventas de los últimos 12 meses y los
eventos que explican meses atípicos.

Aquí están solo los cálculos, sin pantalla, para poder probarlos aparte. No cambian ninguna cifra del modelo de pronóstico: usan la
venta esperada y el error medido en la validación (el mismo que ya muestran Predicción y Recomendaciones).

* Rango probable: la venta esperada más y menos el error que ese producto tuvo en la validación. No es una garantía: es el margen
  en que se movió el pronóstico cuando se comparó con lo que de verdad se vendió.
* Riesgo de quedarse sin stock: se estima con una distribución normal (la misma idea del margen de seguridad de las
  recomendaciones): se compara el stock con lo que se espera vender mientras llega la reposición y con cuánto suele equivocarse el
  pronóstico en ese producto. Es una estimación para decidir, no una probabilidad exacta.
"""

from __future__ import annotations

import math
from datetime import date
from statistics import NormalDist

# si el producto no tiene error medido, se usa un error típico según su confiabilidad (lo mismo que se le explica al usuario)
ERROR_SEGUN_CONFIABILIDAD = {"Alta": 0.20, "Media": 0.40, "Baja": 0.70}
ERROR_SIN_DATO = 0.70  # sin dato ni confiabilidad: se asume el caso más cauteloso
ERROR_MINIMO, ERROR_MAXIMO = 0.10, 1.00
FACTOR_MAE_A_SIGMA = 1.25  # la desviación típica es aproximadamente 1,25 veces el error absoluto medio


def error_relativo(error_validacion_pct, confiabilidad) -> float:
    """Error del pronóstico como fracción (0,35 = 35 %), acotado para que el rango no sea ni absurdamente estrecho ni enorme."""
    if error_validacion_pct is not None:
        error = float(error_validacion_pct) / 100.0
    else:
        error = ERROR_SEGUN_CONFIABILIDAD.get(confiabilidad, ERROR_SIN_DATO)
    return min(max(error, ERROR_MINIMO), ERROR_MAXIMO)



def rango_probable(demanda: float, error: float) -> tuple[int, int]:
    """(mínimo, máximo) de unidades que es razonable esperar vender el mes: el mínimo se redondea hacia abajo y el máximo hacia arriba."""
    demanda = max(float(demanda), 0.0)
    return int(math.floor(max(demanda * (1 - error), 0.0))), int(math.ceil(demanda * (1 + error)))


def riesgo_de_quiebre(stock, demanda: float, error: float, dias_reposicion: int, demanda_minima: float = 0.5) -> dict | None:
    """
    Probabilidad aproximada de que el stock no alcance hasta que llegue la reposición.
    Devuelve None si no hay stock registrado. `nivel`: Bajo (menos de 10 %), Medio (10 a 35 %) o Alto (35 % o más).
    """
    if stock is None:
        return None
    stock, demanda = float(stock), float(demanda)
    espera = demanda * dias_reposicion / 30.0
    if demanda < demanda_minima:
        probabilidad = 0.0  # casi no se vende: no hay riesgo de quedarse sin producto
    elif stock <= 0:
        probabilidad = 1.0
    else:
        sigma = max(FACTOR_MAE_A_SIGMA * error * demanda * math.sqrt(dias_reposicion / 30.0), 0.5)
        probabilidad = 1.0 - NormalDist().cdf((stock - espera) / sigma)
    pct = min(max(round(probabilidad * 100), 0), 100)
    nivel = "Bajo" if pct < 10 else "Medio" if pct < 35 else "Alto"
    return {"probabilidad_pct": pct, "nivel": nivel, "espera": espera}


def ventas_por_mes(filas, hasta: date, meses: int = 12) -> list[dict]:
    """
    filas: pares (fecha, cantidad). Devuelve una fila por cada uno de los últimos `meses` meses (incluido el de `hasta`), en orden
    cronológico y con 0 donde no hubo ventas, para que se vea también cuándo NO se vendió.
    """
    claves = []
    anio, mes = hasta.year, hasta.month
    for _ in range(meses):
        claves.append((anio, mes))
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
    claves.reverse()
    total = {c: 0 for c in claves}
    for fecha, cantidad in filas:
        clave = (fecha.year, fecha.month)
        if clave in total:
            total[clave] += int(cantidad)
    maximo = max(total.values()) or 1
    return [{"anio": a, "mes": m, "etiqueta": f"{m:02d}/{a}", "unidades": total[(a, m)], "ancho_pct": round(total[(a, m)] * 100 / maximo)}
            for a, m in claves]
