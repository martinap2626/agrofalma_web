from app.extensions import db


class StockPorSede(db.Model):
    __tablename__ = "stock_por_sede"

    id_stock = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    id_sede = db.Column(db.Integer, db.ForeignKey("sedes.id_sede"), nullable=False)
    cantidad_actual = db.Column(db.Integer, nullable=False, default=0)
    fecha_actualizacion = db.Column(db.DateTime, server_default=db.func.now())

    producto = db.relationship("Producto", back_populates="stock_por_sede")
    sede = db.relationship("Sede")

    __table_args__ = (
        db.UniqueConstraint("id_producto", "id_sede", name="uk_stock_producto_sede"),
    )

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<StockPorSede prod={self.id_producto} sede={self.id_sede} cant={self.cantidad_actual}>"
