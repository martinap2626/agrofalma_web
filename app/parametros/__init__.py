"""
Parámetros del sistema que el Administrador puede ajustar desde la pantalla «Parámetros» (sin tocar código ni reiniciar):

* tiempo de reposición (días que tarda en llegar la mercadería),
* nivel de protección contra quedarse sin stock de cada clase ABC (A, B y C),
* descuento máximo que un Trabajador puede dar sin autorización.

Cada valor se guarda en la tabla `parametros_sistema`; si no hay valor guardado se usa el de fábrica (el mismo que antes estaba
fijo en el código). Los cambios quedan en la bitácora con el valor anterior y el nuevo. Si la base no responde se usan los
valores de fábrica, para que ninguna pantalla se caiga por esto.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from analitica_job.pipeline.recomendaciones import Parametros
from app.extensions import db
from app.models import ParametroSistema


@dataclass(frozen=True)
class Definicion:
    clave: str
    etiqueta: str
    ayuda: str
    unidad: str
    minimo: float
    maximo: float
    defecto: float
    entero: bool = True


DEFINICIONES = [
    Definicion("tiempo_reposicion_dias", "Días que tarda en llegar la reposición",
               "Desde que se hace el pedido hasta que la mercadería está en el almacén. Con más días se recomienda tener más stock.",
               "días", 1, 90, 15),
    Definicion("proteccion_A", "Protección de los productos de clase A",
               "Probabilidad aproximada de no quedarse sin stock en los productos que más pesan en las ventas.", "%", 50, 99, 95),
    Definicion("proteccion_B", "Protección de los productos de clase B",
               "Igual, para los productos de venta intermedia.", "%", 50, 99, 90),
    Definicion("proteccion_C", "Protección de los productos de clase C",
               "Igual, para los productos de poca venta (conviene no acumular de más).", "%", 50, 99, 80),
    Definicion("descuento_max_trabajador_pct", "Descuento máximo del Trabajador",
               "Rebaja sobre el precio de lista que un Trabajador puede dar sin autorización. Más que eso se rechaza.",
               "%", 0, 100, 10, entero=False),
]
POR_CLAVE = {d.clave: d for d in DEFINICIONES}
ETIQUETAS = {d.clave: d.etiqueta[0].lower() + d.etiqueta[1:] for d in DEFINICIONES}


def _defecto(d: Definicion) -> float:
    if d.clave == "descuento_max_trabajador_pct":
        try:
            return float(current_app.config.get("DESCUENTO_MAX_TRABAJADOR_PCT", d.defecto))
        except RuntimeError:  # fuera de una aplicación
            return d.defecto
    return d.defecto


def valores_guardados() -> dict[str, float]:
    try:
        return {f.clave: float(f.valor) for f in ParametroSistema.query.all() if f.clave in POR_CLAVE}
    except RuntimeError:  # sin aplicación activa (cálculos sueltos): valores de fábrica
        return {}
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        return {}


def valores() -> dict[str, float]:
    """Valor vigente de cada parámetro: el guardado o, si no hay, el de fábrica."""
    guardados = valores_guardados()
    return {d.clave: guardados.get(d.clave, _defecto(d)) for d in DEFINICIONES}


def obtener(clave: str) -> float:
    return valores()[clave]


def validar(formulario) -> tuple[dict[str, float], list[str]]:
    """Lee el formulario; devuelve los valores válidos y los mensajes de error en palabras sencillas."""
    nuevos, errores = {}, []
    for d in DEFINICIONES:
        texto = (formulario.get(d.clave) or "").strip().replace(",", ".")
        try:
            numero = float(texto)
            if numero != numero or numero in (float("inf"), float("-inf")):
                raise ValueError
        except ValueError:
            errores.append(f"«{d.etiqueta}»: escribe un número.")
            continue
        if not d.minimo <= numero <= d.maximo:
            errores.append(f"«{d.etiqueta}»: debe estar entre {d.minimo:g} y {d.maximo:g} {d.unidad}.")
            continue
        if d.entero and numero != int(numero):
            errores.append(f"«{d.etiqueta}»: usa un número entero.")
            continue
        nuevos[d.clave] = int(numero) if d.entero else round(numero, 1)
    return nuevos, errores


def guardar(nuevos: dict[str, float], usuario_login: str) -> None:
    """Guarda (o actualiza) los valores. Quien llama hace el commit."""
    for clave, valor in nuevos.items():
        fila = db.session.get(ParametroSistema, clave)
        if fila is None:
            db.session.add(ParametroSistema(clave=clave, valor=f"{valor:g}", actualizado_por=usuario_login[:30]))
        else:
            fila.valor, fila.actualizado_por = f"{valor:g}", usuario_login[:30]


def parametros_vigentes() -> Parametros:
    """Parámetros del cálculo de abastecimiento con lo que el Administrador dejó guardado."""
    v = valores()
    base = Parametros()
    z = dict(base.z_por_clase)
    for clase in ("A", "B", "C"):
        pct = v[f"proteccion_{clase}"]
        if round(NormalDist().cdf(z[clase]) * 100) != round(pct):  # si no cambió, se conserva el z de siempre
            z[clase] = NormalDist().inv_cdf(pct / 100.0)
    return Parametros(tiempo_reposicion_dias=int(v["tiempo_reposicion_dias"]), z_por_clase=z)
