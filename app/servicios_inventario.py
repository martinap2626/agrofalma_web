"""
Cambios de stock con kárdex (historial de movimientos).

Todo cambio del stock de un producto pasa por aquí, para que:

* sea ATÓMICO: la resta se hace dentro de la propia sentencia UPDATE con la condición "solo si alcanza"
  (UPDATE ... SET cantidad = cantidad - n WHERE cantidad >= n). Así dos ventas simultáneas del mismo producto no pueden
  llevarse la misma última unidad: la segunda ve que ya no alcanza y se rechaza. Leer el valor, restarlo en Python y
  guardarlo (lo que se hacía antes) permitía que ambas leyeran el mismo número y la segunda pisara a la primera.
* deje un MovimientoInventario con quién, cuándo, cuánto, por qué y cuánto quedó.
* no haya interbloqueos: cuando una operación toca varios productos, los procesa siempre en orden de id.

Las funciones NO hacen commit: forman parte de la transacción de quien las llama (la venta, la anulación, el ingreso...).
"""

from __future__ import annotations

from sqlalchemy import select, update

from app.extensions import db
from app.models import MovimientoInventario, StockPorSede

CANTIDAD_MAXIMA = 1_000_000


class StockInsuficiente(Exception):
    """El producto no tiene (o ya no tiene) unidades suficientes en la sede."""

    def __init__(self, id_producto: int):
        super().__init__(f"Stock insuficiente del producto {id_producto}")
        self.id_producto = id_producto


# stock actual de un producto en una sede, leído dentro de la transacción en curso (None si no hay fila)
def _stock_actual(id_producto: int, id_sede: int) -> int | None:
    return db.session.execute(
        select(StockPorSede.cantidad_actual).where(
            StockPorSede.id_producto == id_producto, StockPorSede.id_sede == id_sede
        )
    ).scalar()


def _registrar(id_producto, id_sede, tipo, cantidad, id_usuario, motivo=None, id_venta=None, token_envio=None):
    db.session.add(
        MovimientoInventario(
            id_producto=id_producto, id_sede=id_sede, tipo=tipo, cantidad=cantidad,
            stock_resultante=_stock_actual(id_producto, id_sede) or 0,
            motivo=(motivo or None) and motivo[:150], id_usuario=id_usuario, id_venta=id_venta,
            token_envio=token_envio,
        )
    )


# suma unidades al stock (crea la fila si ese producto nunca tuvo stock en la sede)
def _sumar(id_producto: int, id_sede: int, cantidad: int) -> None:
    actualizadas = db.session.execute(
        update(StockPorSede)
        .where(StockPorSede.id_producto == id_producto, StockPorSede.id_sede == id_sede)
        .values(cantidad_actual=StockPorSede.cantidad_actual + cantidad, fecha_actualizacion=db.func.now())
        .execution_options(synchronize_session=False)
    ).rowcount
    if actualizadas == 0:
        db.session.add(StockPorSede(id_producto=id_producto, id_sede=id_sede, cantidad_actual=cantidad, origen="real"))
        db.session.flush()


def descontar_por_venta(id_sede: int, cantidades: dict[int, int], id_usuario: int, id_venta: int) -> None:
    """Resta lo vendido. Lanza StockInsuficiente (sin dejar nada a medias en esta operación) si algún producto no alcanza."""
    for id_producto in sorted(cantidades):
        cantidad = cantidades[id_producto]
        actualizadas = db.session.execute(
            update(StockPorSede)
            .where(
                StockPorSede.id_producto == id_producto, StockPorSede.id_sede == id_sede,
                StockPorSede.cantidad_actual >= cantidad,
            )
            .values(cantidad_actual=StockPorSede.cantidad_actual - cantidad, fecha_actualizacion=db.func.now())
            .execution_options(synchronize_session=False)
        ).rowcount
        if actualizadas != 1:
            raise StockInsuficiente(id_producto)
        _registrar(id_producto, id_sede, "venta", -cantidad, id_usuario, id_venta=id_venta)


def devolver_por_anulacion(id_sede: int, cantidades: dict[int, int], id_usuario: int, id_venta: int, motivo: str) -> None:
    """Devuelve al almacén lo que había salido por una venta que se anula."""
    for id_producto in sorted(cantidades):
        _sumar(id_producto, id_sede, cantidades[id_producto])
        _registrar(id_producto, id_sede, "anulacion", cantidades[id_producto], id_usuario, motivo, id_venta)


def ingresar(id_producto: int, id_sede: int, cantidad: int, id_usuario: int, motivo: str | None,
             token_envio: str | None = None) -> int:
    """Ingreso de mercadería comprada o de producción propia. Devuelve el stock resultante."""
    if not 0 < cantidad <= CANTIDAD_MAXIMA:
        raise ValueError("La cantidad de ingreso debe estar entre 1 y 1 000 000.")
    _sumar(id_producto, id_sede, cantidad)
    _registrar(id_producto, id_sede, "ingreso", cantidad, id_usuario, motivo, token_envio=token_envio)
    return _stock_actual(id_producto, id_sede) or 0


def ajustar_a_conteo(id_producto: int, id_sede: int, conteo: int, id_usuario: int, motivo: str,
                     token_envio: str | None = None) -> int:
    """
    Fija el stock al número contado físicamente. Registra la diferencia como movimiento y marca el stock de ese
    producto como REAL (deja de ser el estimado provisional). Devuelve la diferencia (conteo - lo que había).
    """
    if not 0 <= conteo <= CANTIDAD_MAXIMA:
        raise ValueError("El conteo debe estar entre 0 y 1 000 000.")
    # se bloquea la fila mientras se calcula la diferencia, para que una venta simultánea no la deje desactualizada
    fila = db.session.execute(
        select(StockPorSede)
        .where(StockPorSede.id_producto == id_producto, StockPorSede.id_sede == id_sede)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    anterior = fila.cantidad_actual if fila is not None else 0
    if fila is None:
        db.session.add(StockPorSede(id_producto=id_producto, id_sede=id_sede, cantidad_actual=conteo, origen="real"))
        db.session.flush()
    else:
        fila.cantidad_actual = conteo
        fila.origen = "real"
        db.session.flush()
    _registrar(id_producto, id_sede, "ajuste", conteo - anterior, id_usuario, motivo, token_envio=token_envio)
    return conteo - anterior
