"""
Simulador de escenarios («¿qué pasa si...?») de la analítica comercial, en lenguaje de negocio.

El usuario elige un producto, un escenario listo (se vende más, se vende menos, la reposición demora, o indica él mismo cuánto
se venderá) y cuánto stock tiene hoy. El resultado se cuenta en palabras (cuánto se espera vender, para cuántos días alcanza el
stock, cuándo se agotaría, cuánto reponer y cuánto cuesta aproximadamente) y con una serie para el gráfico de agotamiento.

El cálculo de fondo es el mismo de las recomendaciones (analitica_job/pipeline/recomendaciones.simular); este módulo solo lee
lo que escribe el usuario, lo valida y arma el resultado. No guarda nada.

Supuesto del gráfico y de los días de alcance: la venta del mes se reparte parejo entre los 30 días (venta del mes / 30 por día).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from analitica_job.pipeline.recomendaciones import simular
from app.parametros import parametros_vigentes

ESCENARIOS = {
    "normal": "Sin cambios (la situación actual)",
    "mas": "Se vende más de lo previsto",
    "menos": "Se vende menos de lo previsto",
    "demora": "La reposición demora más",
    "personalizado": "Yo indico cuánto se venderá",
}

# (mínimo, máximo, valor por defecto) de lo que se escribe en cada escenario
LIMITES = {
    "mas": (1.0, 500.0, 20.0),            # % más de venta
    "menos": (1.0, 90.0, 20.0),           # % menos de venta
    "demora": (1.0, 120.0, 30.0),         # días que tarda la reposición
    "personalizado": (0.0, 1_000_000.0, None),  # unidades que se venderán en el mes
}
STOCK_MAXIMO = 1_000_000.0
DIAS_DEL_MES = 30.0
HORIZONTE_MINIMO_DIAS = 45
HORIZONTE_MAXIMO_DIAS = 90


def _unidades(n: float) -> str:
    n = int(round(n))
    return f"{n} unidad" if n == 1 else f"{n} unidades"


def _numero(texto, minimo: float, maximo: float):
    """Convierte lo escrito en un número dentro del rango; devuelve None si está vacío, es texto o está fuera de rango."""
    try:
        valor = float(str(texto).strip().replace(",", "."))
    except ValueError:
        return None
    return valor if valor == valor and minimo <= valor <= maximo else None


def leer_entrada(args, stock_registrado: int | None = None) -> dict:
    """
    Lee y valida lo que llega en la dirección (GET). Devuelve el escenario, el valor de cada escenario, el stock y los avisos
    de lo que no se pudo usar (se reemplaza por un valor por defecto, nunca se rompe la pantalla).
    """
    avisos = []
    escenario = args.get("escenario", "normal")
    if escenario not in ESCENARIOS:
        escenario = "normal"

    valores = {}
    for clave, (minimo, maximo, defecto) in LIMITES.items():
        texto = args.get(f"valor_{clave}", "").strip()
        valor = _numero(texto, minimo, maximo) if texto else None
        if texto and valor is None and escenario == clave:
            avisos.append(f"El valor escrito para «{ESCENARIOS[clave]}» no es válido (debe estar entre {minimo:g} y {maximo:g}); se usó {defecto:g}." if defecto is not None
                          else f"El valor escrito para «{ESCENARIOS[clave]}» no es válido (debe estar entre {minimo:g} y {maximo:g}).")
        valores[clave] = valor if valor is not None else defecto

    texto_stock = args.get("stock", "").strip()
    stock = _numero(texto_stock, 0.0, STOCK_MAXIMO) if texto_stock else None
    if texto_stock and stock is None:
        avisos.append("El stock escrito no es válido (debe ser un número entre 0 y 1 000 000); se usó el stock registrado.")
    if stock is None:
        stock = stock_registrado
    if escenario == "personalizado" and valores["personalizado"] is None:
        avisos.append("Escribe cuántas unidades se venderán en el mes para ver el resultado de este escenario.")
        escenario = "normal"
    return {"escenario": escenario, "valores": valores, "stock": stock, "avisos": avisos}


DIAS_MAXIMOS_CON_FECHA = 365  # si el stock dura más de un año no se calcula una fecha de agotamiento


def _fecha_de_agotamiento(hoy: date, dias: float | None) -> date | None:
    if dias is None or dias > DIAS_MAXIMOS_CON_FECHA:
        return None
    return hoy + timedelta(days=int(math.floor(dias)))


def calcular(rec, entrada: dict, hoy: date | None = None) -> dict:
    """
    rec: una recomendación (demanda_prevista, stock_seguridad, clase_abc y producto con nombre y precio).
    Devuelve todo lo que necesita la pantalla: cifras de la situación normal y del escenario, frases, y la serie del gráfico.
    """
    hoy = hoy or date.today()
    demanda = float(rec.demanda_prevista)
    margen = float(rec.stock_seguridad or 0)
    stock = entrada["stock"]
    escenario, valores = entrada["escenario"], entrada["valores"]
    dias_normal = parametros_vigentes().tiempo_reposicion_dias

    demanda_esc, dias_esc = demanda, dias_normal
    if escenario == "mas":
        demanda_esc = demanda * (1 + valores["mas"] / 100.0)
        titulo = f"Si se vende un {valores['mas']:g} % más"
    elif escenario == "menos":
        demanda_esc = demanda * (1 - valores["menos"] / 100.0)
        titulo = f"Si se vende un {valores['menos']:g} % menos"
    elif escenario == "demora":
        dias_esc = int(valores["demora"])
        titulo = f"Si la reposición demora {dias_esc} días"
    elif escenario == "personalizado":
        demanda_esc = valores["personalizado"]
        titulo = f"Si se venden {_unidades(demanda_esc)} este mes"
    else:
        titulo = "Situación actual (sin cambios)"

    base = simular(demanda, margen, rec.clase_abc, 0, dias_normal, stock)
    esc = simular(demanda, margen, rec.clase_abc, 0, dias_esc, stock, demanda_escenario=max(0.0, demanda_esc))
    precio = float(rec.producto.precio_unitario or 0)

    def completar(r, dias_reposicion):
        dias = r["dias_alcanza"]
        r["fecha_agota"] = _fecha_de_agotamiento(hoy, dias)
        r["dias_reposicion"] = dias_reposicion
        r["antes_de_reposicion"] = None if dias is None else dias < dias_reposicion
        r["valor_referencia"] = r["cantidad"] * precio
        return r

    base, esc = completar(base, dias_normal), completar(esc, dias_esc)
    diferencia = esc["cantidad"] - base["cantidad"]

    return {
        "producto": rec.producto.nombre_producto, "clase": rec.clase_abc, "titulo": titulo, "escenario": escenario,
        "stock": None if stock is None else int(round(stock)), "con_stock": stock is not None,
        "base": base, "esc": esc, "diferencia": diferencia, "cambio_demanda": esc["demanda"] - base["demanda"],
        "frases": _frases(base, esc, escenario, valores, demanda, stock, diferencia),
        "grafico": _serie_del_grafico(base, esc, stock, hoy),
    }


def _frases(base, esc, escenario, valores, demanda, stock, diferencia) -> list[tuple[str, str]]:
    """Cuenta el resultado en palabras: lista de (tipo, texto); tipo = 'normal', 'alerta' o 'bien'."""
    frases = []
    # 1. cuánto se espera vender
    if escenario == "normal":
        frases.append(("normal", f"Normalmente se espera vender unas {_unidades(base['demanda'])} este mes."))
    elif escenario == "demora":
        frases.append(("normal", f"Se espera vender lo de siempre ({_unidades(base['demanda'])} al mes), pero la reposición tardaría "
                                 f"{esc['dias_reposicion']} días en llegar, en lugar de los {base['dias_reposicion']} habituales."))
    else:
        frases.append(("normal", f"{_unidades(esc['demanda']).capitalize()} este mes en el escenario, frente a {_unidades(base['demanda'])} normalmente "
                                 f"({'+' if esc['demanda'] >= base['demanda'] else '-'}{_unidades(abs(esc['demanda'] - base['demanda']))})."))

    # 2. cuánto dura el stock
    if stock is None:
        frases.append(("normal", "No hay un stock registrado para este producto: escribe cuántas unidades tienes hoy para ver cuándo se agotarían."))
    elif esc["dias_alcanza"] is None:
        frases.append(("bien", f"Hoy hay {_unidades(stock)} y no se prevén ventas, así que el stock no se agota."))
    elif stock == 0:
        frases.append(("alerta", f"Hoy no hay unidades en el almacén: se vendería poco o nada hasta que llegue la reposición (tarda {esc['dias_reposicion']} días)."))
    elif esc["fecha_agota"] is None:
        frases.append(("bien", f"Con {_unidades(stock)} hoy, el stock te alcanza para más de un año con ese ritmo de venta."))
    else:
        fecha = esc["fecha_agota"].strftime("%d/%m/%Y")
        frases.append(("alerta" if esc["antes_de_reposicion"] else "normal",
                       f"Con {_unidades(stock)} hoy, te alcanza para unos {esc['dias_alcanza']:g} días: se agotaría cerca del {fecha}."))
        if esc["antes_de_reposicion"]:
            frases.append(("alerta", f"Eso es antes de que llegue la reposición (tarda {esc['dias_reposicion']} días): habría días sin producto."))
        else:
            frases.append(("bien", f"Alcanza hasta que llegue la reposición (tarda {esc['dias_reposicion']} días)."))

    # 3. qué hacer
    if stock is None:
        frases.append(("normal", f"Conviene tener disponibles {_unidades(esc['objetivo'])} este mes (lo esperado, lo que se vende mientras llega la reposición y un margen de seguridad)."))
    elif esc["nivel"] == "sin_accion":
        frases.append(("bien", "Por ahora no hace falta reponer."))
    else:
        texto = f"Conviene reponer {_unidades(esc['cantidad'])} para llegar a {_unidades(esc['objetivo'])} en total."
        if escenario != "normal" and diferencia != 0:
            texto += f" Son {_unidades(abs(diferencia))} {'más' if diferencia > 0 else 'menos'} que en la situación actual."
        frases.append(("alerta" if esc["nivel"] == "urgente" else "normal", texto))
    return frases


def _serie_del_grafico(base, esc, stock, hoy) -> dict | None:
    """Stock día a día (situación normal y escenario) para el gráfico de agotamiento; None si no hay stock."""
    if stock is None:
        return None
    ritmos = [r["demanda"] / DIAS_DEL_MES for r in (base, esc)]
    # el gráfico llega hasta que se agota el que más dura (con un tope) o, al menos, un poco más allá de la reposición
    agota = [stock / r for r in ritmos if r > 0]
    horizonte = max(HORIZONTE_MINIMO_DIAS, int(max(esc["dias_reposicion"], base["dias_reposicion"]) * 1.5))
    if agota:
        horizonte = max(horizonte, int(math.ceil(max(agota))) + 5)
    horizonte = min(horizonte, HORIZONTE_MAXIMO_DIAS)
    dias = list(range(horizonte + 1))
    return {
        "etiquetas": [(hoy + timedelta(days=d)).strftime("%d/%m") for d in dias],
        "normal": [round(max(0.0, stock - ritmos[0] * d), 2) for d in dias],
        "escenario": [round(max(0.0, stock - ritmos[1] * d), 2) for d in dias],
        "dia_reposicion": esc["dias_reposicion"] if esc["dias_reposicion"] <= horizonte else None,
        "punto_reposicion": esc["punto_reposicion"],
    }
