from app.extensions import db


class DetalleVenta(db.Model):
    """
    Línea de producto de una venta. `precio_unitario` se copia del catálogo
    al momento de vender (no se referencia en vivo) para que el histórico de
    ventas no cambie si después se edita el precio del producto.
    """

    __tablename__ = "detalle_ventas"
    __table_args__ = (db.CheckConstraint("cantidad > 0", name="ck_detalle_cantidad_positiva"),)

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_venta = db.Column(db.Integer, db.ForeignKey("ventas.id_venta"), nullable=False)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False, default=1)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    venta = db.relationship("Venta", back_populates="detalle")
    producto = db.relationship("Producto")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<DetalleVenta venta={self.id_venta} prod={self.id_producto} cant={self.cantidad}>"
