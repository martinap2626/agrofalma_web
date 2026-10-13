from datetime import datetime

from app.extensions import db

TIPOS_MOVIMIENTO = {
    "ingreso": "Ingreso de mercadería o producción propia",
    "ajuste": "Ajuste por conteo físico",
    "venta": "Salida por venta",
    "anulacion": "Devolución por venta anulada",
}


class MovimientoInventario(db.Model):
    """
    Kárdex: cada cambio del stock de un producto en una sede deja una fila (quién, cuándo, cuánto, por qué y cuánto
    quedó). El stock actual vive en StockPorSede; esta tabla explica cómo se llegó a él. Es de solo-agregar, como la
    bitácora: la aplicación nunca la actualiza ni la borra.
    """

    __tablename__ = "movimientos_inventario"
    __table_args__ = (
        db.Index("ix_mov_producto_fecha", "id_producto", "fecha_hora"),
        db.Index("ix_mov_fecha", "fecha_hora"),
    )

    id_movimiento = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True, autoincrement=True)
    fecha_hora = db.Column(db.DateTime, nullable=False, default=datetime.now)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    id_sede = db.Column(db.Integer, db.ForeignKey("sedes.id_sede"), nullable=False)
    tipo = db.Column(db.String(12), nullable=False)  # ingreso | ajuste | venta | anulacion
    cantidad = db.Column(db.Integer, nullable=False)  # con signo: positivo entra, negativo sale
    stock_resultante = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(150))
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False)
    id_venta = db.Column(db.Integer, db.ForeignKey("ventas.id_venta"))
    # código único del formulario (solo ingresos y conteos): evita sumar dos veces el mismo ingreso por un doble clic
    token_envio = db.Column(db.String(40), unique=True)

    producto = db.relationship("Producto")
    sede = db.relationship("Sede")
    usuario = db.relationship("Usuario")
    venta = db.relationship("Venta")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<MovimientoInventario {self.tipo} prod={self.id_producto} {self.cantidad:+d}>"
