"""
Historial de aciertos del pronóstico: compara lo que el sistema esperaba vender en meses que ya terminaron con lo que de verdad se
vendió. Es la respuesta a «¿puedo fiarme de este pronóstico?» con evidencia propia del negocio, no con una cifra de laboratorio.

Aquí están solo los cálculos, sin pantalla ni base de datos, para poder probarlos aparte. No cambian ningún pronóstico: solo lo miden.

* Acierto del mes: 100 % menos el error total (suma de las diferencias entre lo esperado y lo vendido de cada producto, dividida
  entre lo vendido). Se acota en 0 %: equivocarse por más del 100 % es simplemente «no acertó».
* Dentro del rango: el producto cuya venta real cayó dentro del rango probable que mostraba su ficha (la venta esperada más y menos
  el error de validación del producto).
"""

from __future__ import annotations

from datetime import date

from app import ficha_producto as ficha


def mes_cerrado(periodo: date, hoy: date) -> bool:
    """Un mes ya terminó cuando hoy está en un mes posterior: el último día de `periodo` ya pasó."""
    return (periodo.year, periodo.month) < (hoy.year, hoy.month)


def evaluar(estimado, real, error_validacion=None, confiabilidad=None) -> dict:
    """Compara lo esperado con lo vendido de un producto en un mes: diferencia en unidades y si cayó dentro del rango probable."""
    estimado, real = float(estimado), float(real)
    minimo, maximo = ficha.rango_probable(estimado, ficha.error_relativo(error_validacion, confiabilidad))
    return {"estimado": estimado, "real": real, "diferencia": real - estimado, "dentro_rango": minimo <= real <= maximo}


def resumir_periodo(filas: list[dict]) -> dict:
    """
    Resume los productos de un mismo mes (cada fila viene de `evaluar`): unidades esperadas y vendidas, acierto, y cuántos productos
    quedaron dentro de su rango probable. El acierto es None si ese mes no se vendió nada (no hay contra qué comparar).
    """
    esperado = sum(f["estimado"] for f in filas)
    vendido = sum(f["real"] for f in filas)
    error_abs = sum(abs(f["diferencia"]) for f in filas)
    acierto = None if vendido <= 0 else max(0, round((1 - error_abs / vendido) * 100))
    dentro = sum(1 for f in filas if f["dentro_rango"])
    return {
        "productos": len(filas), "esperado": round(esperado), "vendido": round(vendido), "acierto_pct": acierto,
        "dentro_rango": dentro, "dentro_rango_pct": round(dentro * 100 / len(filas)) if filas else None,
        "sesgo": None if vendido <= 0 else ("sobreestima" if esperado > vendido * 1.10 else "subestima" if esperado < vendido * 0.90 else "equilibrado"),
    }


def texto_acierto(resumen: dict) -> str:
    """Frase para el usuario sobre un mes, sin términos técnicos."""
    if resumen["acierto_pct"] is None:
        return "Ese mes no se registraron ventas de estos productos, no hay contra qué comparar."
    sesgo = {"sobreestima": "Se esperaba vender más de lo que se vendió.", "subestima": "Se vendió más de lo que se esperaba.",
             "equilibrado": "En total, lo esperado y lo vendido quedaron parecidos."}[resumen["sesgo"]]
    return f"Se esperaban {resumen['esperado']} unidades y se vendieron {resumen['vendido']}. {sesgo}"
