"""
Plan de campaña: cuánto se espera vender de cada producto en una CAMPAÑA completa
(grande: enero a junio; chica: julio a diciembre) y cómo va la campaña en curso.

Por qué por campaña y no solo por mes: el negocio decide por campaña agrícola, y al
sumar seis meses los errores de un mes se compensan con los de otro (error por
producto de la campaña grande 2026: ~21% frente a ~60% del pronóstico de un solo mes).

Método (simple a propósito: hay solo 7 campañas de historia, no alcanza para entrenar
un modelo):

  - Campaña SIN avance: lo vendido en la misma campaña del año anterior.
  - Campaña EN CURSO: lo ya vendido + lo que faltó vender el año anterior en los meses que
    restan, multiplicado por el RITMO de este año (vendido hasta hoy ÷ lo que se había vendido
    en esos mismos meses el año anterior). Así el pronóstico se corrige a medida que pasa la campaña.
  - Campaña PRÓXIMA: lo vendido en esa misma campaña el año anterior (sin ajuste por crecimiento).

El error típico se mide con los datos: se simula la campaña terminada más reciente con el mismo método
y el mismo número de meses conocidos, y se reporta el error por clase ABC.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MESES = {"G": list(range(1, 7)), "C": list(range(7, 13))}
NOMBRES = {"G": "Campaña grande (enero a junio)", "C": "Campaña chica (julio a diciembre)"}
RITMO_MIN, RITMO_MAX = 0.25, 4.0   # el ritmo se acota para que un mes raro no dispare el pronóstico
# campañas con año anterior disponible, para medir el error del método
CAMPANAS_PRUEBA = [(2024, "G"), (2024, "C"), (2025, "G"), (2025, "C"), (2026, "G")]


def tipo_de_mes(mes: int) -> str:
    return "G" if mes <= 6 else "C"


def _mensual(df: pd.DataFrame) -> pd.DataFrame:
    """Matriz producto x (año, mes) con las unidades vendidas."""
    return df.groupby(["base", "anio", "mes"])["unidades"].sum().unstack(["anio", "mes"], fill_value=0)


def _suma(m: pd.DataFrame, anio: int, meses: list[int], productos) -> pd.Series:
    cols = [(anio, x) for x in meses if (anio, x) in m.columns]
    if not cols:
        return pd.Series(0.0, index=productos)
    return m.reindex(productos, fill_value=0)[cols].sum(axis=1).astype(float)


def _meses_con_venta(m: pd.DataFrame, anio: int, meses: list[int], productos) -> pd.Series:
    cols = [(anio, x) for x in meses if (anio, x) in m.columns]
    if not cols:
        return pd.Series(0, index=productos)
    return (m.reindex(productos, fill_value=0)[cols] > 0).sum(axis=1)


def _pronostico(m, productos, anio: int, tipo: str, k: int) -> pd.DataFrame:
    """Pronóstico de la campaña (anio, tipo) sabiendo los primeros k meses de esa campaña."""
    meses = MESES[tipo]
    conocidos, resto = meses[:k], meses[k:]
    vendido = _suma(m, anio, conocidos, productos)
    ref_total = _suma(m, anio - 1, meses, productos)
    ref_conocidos = _suma(m, anio - 1, conocidos, productos)
    ref_resto = _suma(m, anio - 1, resto, productos)
    if k and ref_conocidos.sum() > 0:
        ritmo = float(np.clip(vendido.sum() / ref_conocidos.sum(), RITMO_MIN, RITMO_MAX))
    else:
        ritmo = 1.0
    esperado_resto = ref_resto * ritmo
    out = pd.DataFrame({
        "producto": list(productos), "vendido": vendido.to_numpy(), "referencia_anio_anterior": ref_total.to_numpy(),
        "esperado_resto": esperado_resto.to_numpy(), "esperado_total": (vendido + esperado_resto).to_numpy(),
    })
    prod_ritmo = (vendido / ref_conocidos).where(ref_conocidos > 0)
    out["ritmo_pct"] = (prod_ritmo * 100).round(1).to_numpy()
    out.attrs["ritmo_global"] = ritmo
    return out


def error_por_clase(m, productos, clases: pd.Series, k: int) -> tuple[dict[str, float], str | None]:
    """
    WAPE del método por clase ABC con k meses de la campaña ya conocidos, medido con la campaña TERMINADA MÁS
    RECIENTE (hoy, la grande de 2026). Se usa solo la más reciente a propósito: en los años anteriores las ventas
    se parecen tanto de un año a otro que el error saldría artificialmente bajo (2-6%) y daría una falsa seguridad.
    Devuelve (errores por clase, id de la campaña usada).
    """
    for anio, tipo in reversed(CAMPANAS_PRUEBA):
        if (anio - 1, MESES[tipo][0]) not in m.columns:
            continue
        real = _suma(m, anio, MESES[tipo], productos).to_numpy()
        if real.sum() == 0:
            continue
        prev = _pronostico(m, productos, anio, tipo, k)["esperado_total"].to_numpy()
        clase = clases.reindex(productos).fillna("C").to_numpy()
        errores = {}
        for c in ("A", "B", "C"):
            mask = clase == c
            if real[mask].sum() > 0:
                errores[c] = round(float(np.abs(real[mask] - prev[mask]).sum() / real[mask].sum() * 100), 1)
        errores["Todas"] = round(float(np.abs(real - prev).sum() / real.sum() * 100), 1)
        return errores, f"{anio}-{tipo}"
    return {}, None


def _confianza(error_clase: float | None, meses_con_venta_ref: int) -> str:
    """Alta/Media/Baja a partir del error típico de la clase; baja un nivel si el producto casi no vendió esa campaña el año pasado."""
    if error_clase is None or np.isnan(error_clase):
        nivel = 2
    elif error_clase <= 15:
        nivel = 0
    elif error_clase <= 35:
        nivel = 1
    else:
        nivel = 2
    if meses_con_venta_ref < 3:
        nivel = min(nivel + 1, 2)
    return ["Alta", "Media", "Baja"][nivel]


def plan_campana(df: pd.DataFrame, clases: pd.Series, productos: list[str], ultimo_anio: int, ultimo_mes: int) -> tuple[pd.DataFrame, dict]:
    """
    Devuelve (filas, meta). `filas` trae una fila por producto y por campaña (en curso y próxima) con:
    producto, campana_id, campana_nombre, tipo, meses_conocidos, vendido, referencia_anio_anterior,
    esperado_resto, esperado_total, ritmo_pct, clase_abc, confianza, error_tipico_pct.
    """
    m = _mensual(df)
    productos = list(productos)
    tipo = tipo_de_mes(ultimo_mes)
    meses = MESES[tipo]
    k = len([x for x in meses if x <= ultimo_mes])
    completa = k == len(meses)

    filas, meta = [], {"ultimo_anio": ultimo_anio, "ultimo_mes": ultimo_mes}

    def armar(anio, t, kk, rol):
        p = _pronostico(m, productos, anio, t, kk)
        errores, campana_prueba = error_por_clase(m, productos, clases, kk)
        ref_meses = _meses_con_venta(m, anio - 1, MESES[t], productos).to_numpy()
        p["ritmo_global"] = p.attrs.get("ritmo_global", 1.0)
        p["campana_id"] = f"{anio}-{t}"
        p["campana_nombre"] = f"{NOMBRES[t]} {anio}"
        p["tipo"] = rol
        p["meses_conocidos"] = kk
        p["clase_abc"] = p["producto"].map(clases).fillna("C")
        p["error_tipico_pct"] = p["clase_abc"].map(lambda c: errores.get(c, np.nan))
        p["confianza"] = [_confianza(e, r) for e, r in zip(p["error_tipico_pct"], ref_meses)]
        meta[rol] = {
            "campana_id": f"{anio}-{t}", "nombre": f"{NOMBRES[t]} {anio}", "meses_conocidos": kk, "meses_totales": 6,
            "vendido": float(p["vendido"].sum()), "esperado_total": float(p["esperado_total"].sum()),
            "referencia_anio_anterior": float(p["referencia_anio_anterior"].sum()),
            "ritmo_global": p.attrs.get("ritmo_global", 1.0), "errores": errores, "campana_prueba": campana_prueba,
        }
        filas.append(p)

    if not completa:
        armar(ultimo_anio, tipo, k, "en_curso")
        siguiente = ("C", ultimo_anio) if tipo == "G" else ("G", ultimo_anio + 1)
    else:
        siguiente = ("C", ultimo_anio) if tipo == "G" else ("G", ultimo_anio + 1)
    armar(siguiente[1], siguiente[0], 0, "proxima")
    plan = pd.concat(filas, ignore_index=True)
    return plan, meta
