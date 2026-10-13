"""
Recomendaciones de abastecimiento (HU-11), construidas a partir de DOS resultados
del pipeline: la PREDICCIÓN de demanda del mes siguiente y la clase ABC de cada
producto. Es una regla de negocio explícita (no una caja negra): cada número se
puede explicar con palabras, y todos los supuestos están en `Parametros`.

Lógica (por producto base, sede principal):

  demanda_prevista      lo que se espera vender el mes siguiente (predicción)
  demanda_en_espera     lo que se venderá mientras llega la reposición: demanda_prevista x (días de reposición / 30)
  margen_de_seguridad   colchón contra el error del pronóstico:
                          z(clase) x 1.25 x (error medio que tuvo el pronóstico de ESE producto en la validación)
                        con z = 1.65 (A), 1.28 (B), 0.84 (C): los productos que más pesan en las ventas (A)
                        se protegen más. Se limita a una fracción de la demanda (A 75%, B 50%, C 35%) para
                        no acumular de más en productos de baja rotación. Si el producto no tiene error
                        medido (poca historia) se usa un porcentaje fijo de la demanda (A 50%, B 35%, C 20%).
  stock_objetivo        demanda_prevista + demanda_en_espera + margen   -> hasta cuánto conviene tener
  punto_de_pedido       demanda_en_espera + margen                       -> por debajo de esto hay que reponer

CON inventario cargado (stock actual conocido):
  stock <= demanda_en_espera : "urgente" (se acabaría antes de que llegue la reposición; en clase C, "media")
  stock <= punto_de_pedido   : "media"   (ya toca reponer)
  si no                      : "sin acción"
  cantidad a reponer = stock_objetivo - stock

SIN inventario: se entrega el PLAN de abastecimiento: cuánto conviene tener disponible de cada producto
(stock_objetivo), ordenado por prioridad (clase ABC), para comparar con lo que haya en almacén.

SUPUESTOS a confirmar con el gerente: el tiempo de reposición (hoy 15 días) y los niveles de protección.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from dataclasses import dataclass, field

import pandas as pd

PRIORIDAD_TEXTO = {"A": "alta", "B": "media", "C": "baja"}


@dataclass
class Parametros:
    tiempo_reposicion_dias: int = 15                                    # SUPUESTO
    z_por_clase: dict = field(default_factory=lambda: {"A": 1.65, "B": 1.28, "C": 0.84})
    tope_margen: dict = field(default_factory=lambda: {"A": 0.75, "B": 0.50, "C": 0.35})      # fracción de la demanda
    margen_sin_error: dict = field(default_factory=lambda: {"A": 0.50, "B": 0.35, "C": 0.20})  # fracción de la demanda
    factor_mae_a_sigma: float = 1.25                                    # desviación típica ≈ 1.25 x error absoluto medio
    demanda_minima: float = 0.5                                         # por debajo, no se recomienda reponer

    @property
    def fraccion_espera(self) -> float:
        return self.tiempo_reposicion_dias / 30.0

    def nivel_proteccion_pct(self, clase: str) -> int:
        """Probabilidad aproximada de no quedarse sin stock (z de una normal), para explicarlo al usuario."""
        return round(NormalDist().cdf(self.z_por_clase[clase]) * 100)


PARAMETROS = Parametros()


def _u(n: float) -> str:
    n = int(round(n))
    return f"{n} unidad" if n == 1 else f"{n} unidades"


def _reparto_texto(reparto: pd.DataFrame | None, producto: str, cantidad: int) -> str:
    """Cómo dividir `cantidad` entre las presentaciones según lo que se vendió en los últimos 12 meses."""
    if reparto is None or reparto.empty or cantidad <= 0:
        return ""
    r = reparto[(reparto["base"] == producto) & (reparto["presentacion"] != "sin presentación indicada")]
    if len(r) < 2:
        return ""
    r = r.sort_values("participacion_pct", ascending=False).head(4)
    total = r["participacion_pct"].sum()
    partes = [(p, cantidad * pct / total) for p, pct in zip(r["presentacion"], r["participacion_pct"])]
    enteros = [int(math.floor(v)) for _, v in partes]
    # el resto va a las presentaciones con mayor parte decimal, para que la suma sea exacta
    for i in sorted(range(len(partes)), key=lambda k: partes[k][1] - enteros[k], reverse=True)[: cantidad - sum(enteros)]:
        enteros[i] += 1
    return "Reparto sugerido por presentación: " + ", ".join(f"{p} {n}" for (p, _), n in zip(partes, enteros) if n > 0) + "."


def calcular_objetivos(fila, parametros: Parametros) -> dict:
    clase = fila["clase_abc"] if fila["clase_abc"] in parametros.z_por_clase else "C"
    D = float(fila["demanda_prevista"])
    mae = fila.get("mae_validacion")
    if mae is not None and not pd.isna(mae):
        margen = min(parametros.z_por_clase[clase] * parametros.factor_mae_a_sigma * float(mae), parametros.tope_margen[clase] * D)
    else:
        margen = parametros.margen_sin_error[clase] * D
    margen = math.ceil(margen)
    espera = D * parametros.fraccion_espera
    return {
        "clase": clase, "D": D, "margen": margen, "espera": espera,
        "objetivo": math.ceil(D + espera + margen), "punto_pedido": math.ceil(espera + margen),
    }


def generar(demanda: pd.DataFrame, inventario: pd.DataFrame | None = None, reparto: pd.DataFrame | None = None,
            parametros: Parametros | None = None) -> pd.DataFrame:
    """
    demanda:    producto, clase_abc, demanda_prevista, [mae_validacion], [confiabilidad]
    inventario: producto, stock_actual   (opcional; los productos sin fila quedan en modo "plan")
    reparto:    base, presentacion, participacion_pct   (opcional)
    Devuelve una fila por producto con: stock_actual (None si no hay), demanda_prevista, stock_seguridad,
    punto_pedido, stock_objetivo, cantidad_sugerida, nivel_urgencia (urgente|media|sin_accion|plan), motivo,
    reparto, clase_abc, confiabilidad, con_inventario.
    """
    parametros = parametros or PARAMETROS
    stock = {} if inventario is None or inventario.empty else dict(zip(inventario["producto"], inventario["stock_actual"]))
    filas = []
    for r in demanda.to_dict("records"):
        o = calcular_objetivos(r, parametros)
        D, clase, margen, objetivo, pp = o["D"], o["clase"], o["margen"], o["objetivo"], o["punto_pedido"]
        confiab = r.get("confiabilidad") or ""
        aviso = " El pronóstico de este producto es poco confiable: tómalo como referencia." if confiab == "Baja" and D >= parametros.demanda_minima else ""
        dias = parametros.tiempo_reposicion_dias
        explicacion = (
            f"{_u(D)} el próximo mes + {_u(o['espera'])} mientras llega la reposición ({dias} días) + "
            f"{_u(margen)} de margen (clase {clase}, {parametros.nivel_proteccion_pct(clase)}% de protección)"
        )
        tiene_stock = r["producto"] in stock
        s = float(stock[r["producto"]]) if tiene_stock else None

        if D < parametros.demanda_minima:
            nivel, cantidad = "sin_accion" if tiene_stock else "plan", 0
            motivo = ("No se prevén ventas el próximo mes" + (f" y hay {_u(s)}" if tiene_stock and s > 0 else "") + ": no hace falta reponer.")
            objetivo = pp = margen = 0
        elif not tiene_stock:
            nivel, cantidad = "plan", objetivo
            motivo = (f"Prioridad {PRIORIDAD_TEXTO[clase]} (clase {clase}). Se espera vender alrededor de {_u(D)} el próximo mes. "
                      f"Conviene tener disponibles {_u(objetivo)}: {explicacion}.{aviso}")
        elif s <= o["espera"]:
            nivel = "urgente" if clase in ("A", "B") else "media"
            cantidad = max(0, math.ceil(objetivo - s))
            motivo = (f"Se esperan alrededor de {_u(D)} el próximo mes y hay {_u(s)}: se acabaría antes de que llegue la reposición. "
                      f"Conviene reponer {_u(cantidad)} para tener {_u(objetivo)} ({explicacion}).{aviso}")
        elif s <= pp:
            nivel = "media"
            cantidad = max(0, math.ceil(objetivo - s))
            motivo = (f"Se esperan alrededor de {_u(D)} el próximo mes y hay {_u(s)}: ya está en el punto de reposición ({_u(pp)}). "
                      f"Conviene reponer {_u(cantidad)} para llegar a {_u(objetivo)}.{aviso}")
        else:
            nivel, cantidad = "sin_accion", 0
            motivo = (f"Se esperan alrededor de {_u(D)} el próximo mes y hay {_u(s)}, por encima del punto de reposición ({_u(pp)}): "
                      "no hace falta reponer todavía.")
        texto_reparto = _reparto_texto(reparto, r["producto"], cantidad if tiene_stock else objetivo)
        filas.append({
            "producto": r["producto"], "stock_actual": None if s is None else int(round(s)), "demanda_prevista": round(D, 2),
            "stock_seguridad": int(margen), "punto_pedido": int(pp), "stock_objetivo": int(objetivo), "cantidad_sugerida": int(cantidad),
            "nivel_urgencia": nivel, "motivo": motivo[:400], "reparto": texto_reparto[:250], "clase_abc": clase,
            "confiabilidad": confiab or None, "con_inventario": bool(tiene_stock),
        })
    return pd.DataFrame(filas)


def simular(demanda_base: float, margen_base: float, clase: str, cambio_demanda_pct: float, dias_reposicion: int,
            stock: float | None, parametros: Parametros | None = None, demanda_escenario: float | None = None) -> dict:
    """
    Simulador de escenarios ("¿qué pasa si...?"): recalcula la recomendación de UN producto con una demanda distinta
    (por ejemplo, +20 % por una campaña fuerte) y/o otro tiempo de reposición, sin tocar los resultados guardados.

    El margen de seguridad se escala con la demanda (si se espera vender más, el error en unidades también crece),
    respetando el mismo tope por clase que usa el cálculo normal. Devuelve las cifras del escenario y, si hay stock,
    cuántos días alcanza y cuánto reponer.
    """
    parametros = parametros or PARAMETROS
    clase = clase if clase in parametros.tope_margen else "C"
    # el cambio puede darse en porcentaje o directamente como la demanda (en unidades) del escenario
    D = max(0.0, float(demanda_escenario)) if demanda_escenario is not None else max(0.0, float(demanda_base) * (1 + cambio_demanda_pct / 100.0))
    escala = (D / demanda_base) if demanda_base > 0 else 1.0
    margen = math.ceil(min(float(margen_base) * escala, parametros.tope_margen[clase] * D)) if D > 0 else 0
    espera = D * dias_reposicion / 30.0
    objetivo = math.ceil(D + espera + margen)
    punto = math.ceil(espera + margen)
    out = {"demanda": D, "margen": margen, "espera": espera, "objetivo": objetivo, "punto_reposicion": punto,
           "dias_alcanza": None, "nivel": "plan", "cantidad": objetivo}
    if D < parametros.demanda_minima:
        out.update(nivel="sin_accion", cantidad=0, objetivo=0, punto_reposicion=0)
    elif stock is not None:
        out["dias_alcanza"] = round(float(stock) / (D / 30.0), 1)
        if stock <= espera:
            out["nivel"] = "urgente" if clase in ("A", "B") else "media"
        elif stock <= punto:
            out["nivel"] = "media"
        else:
            out["nivel"] = "sin_accion"
        out["cantidad"] = max(0, math.ceil(objetivo - stock)) if out["nivel"] != "sin_accion" else 0
    return out
