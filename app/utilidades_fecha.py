"""
Rangos de fecha "sargables": en vez de comparar DATE(fecha_hora) = ... o
EXTRACT(YEAR/MONTH FROM fecha_hora) = ..., que obligan a MySQL a calcular la
función en CADA fila de la tabla (y a ignorar los índices), se filtra por un
rango  fecha_hora >= inicio AND fecha_hora < fin  que sí puede usar el índice
y se mantiene rápido aunque la tabla de ventas crezca a cientos de miles de filas.
"""

from datetime import date, datetime, time, timedelta


# [inicio, fin) de un día
def rango_dia(dia: date):
    return datetime.combine(dia, time.min), datetime.combine(dia + timedelta(days=1), time.min)


# [inicio, fin) desde el primer instante de `desde` hasta el último de `hasta` (inclusive)
def rango_fechas(desde: date, hasta: date):
    return datetime.combine(desde, time.min), datetime.combine(hasta + timedelta(days=1), time.min)


# [inicio, fin) de un mes calendario
def rango_mes(anio: int, mes: int):
    inicio = date(anio, mes, 1)
    fin = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return datetime.combine(inicio, time.min), datetime.combine(fin, time.min)
