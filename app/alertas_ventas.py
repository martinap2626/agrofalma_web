"""
Alertas de ventas inusuales del mes en curso (pantalla de Recomendaciones).

Compara lo que un producto lleva vendido en el mes con la parte de la venta esperada que correspondería a esta altura del mes
(venta esperada x días transcurridos / días del mes). Avisa en dos casos:

* «más de lo normal»: lleva vendido el doble o más de lo que tocaba y la diferencia es de al menos `MINIMO_UNIDADES` unidades
  (puede agotar el stock antes de lo previsto);
* «menos de lo normal»: pasada la mitad del mes, lleva el 30 % o menos de lo que tocaba y se esperaban al menos `MINIMO_UNIDADES`
  unidades a esta altura (puede quedar stock sin salir).

Es una alerta para mirar el producto, no una conclusión: un pedido grande de un solo cliente o una campaña pueden explicarla. Los
cálculos están aquí, sin pantalla, para poder probarlos.
"""

from __future__ import annotations

MINIMO_UNIDADES = 5
FACTOR_MAS, FACTOR_MENOS = 2.0, 0.30
MITAD_DEL_MES = 0.5


def detectar_ventas_inusuales(productos: list[dict], dia: int, dias_del_mes: int) -> list[dict]:
    """
    productos: lista de {"id_producto", "nombre", "vendido", "esperado"} (esperado = venta esperada de TODO el mes).
    Devuelve las alertas ordenadas por diferencia absoluta (las más grandes primero), cada una con el tipo («mas» o «menos»),
    lo vendido, lo que tocaba a esta altura y la venta esperada del mes.
    """
    fraccion = min(max(dia / dias_del_mes, 0.0), 1.0)
    alertas = []
    for p in productos:
        esperado_mes, vendido = float(p["esperado"]), float(p["vendido"])
        tocaba = esperado_mes * fraccion
        if tocaba <= 0:
            continue
        if vendido >= tocaba * FACTOR_MAS and vendido - tocaba >= MINIMO_UNIDADES:
            tipo = "mas"
        elif fraccion >= MITAD_DEL_MES and tocaba >= MINIMO_UNIDADES and vendido <= tocaba * FACTOR_MENOS:
            tipo = "menos"
        else:
            continue
        alertas.append({"id_producto": p["id_producto"], "nombre": p["nombre"], "tipo": tipo, "vendido": int(round(vendido)),
                        "tocaba": int(round(tocaba)), "esperado_mes": int(round(esperado_mes)), "diferencia": abs(vendido - tocaba)})
    return sorted(alertas, key=lambda a: -a["diferencia"])
