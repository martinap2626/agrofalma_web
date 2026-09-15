from app.extensions import db


class SegmentacionProducto(db.Model):
    """
    Segmentación ABC de productos por periodo (HU-10, Sprint 3): clasifica
    cada producto según su participación acumulada en las ventas del
    periodo (regla de Pareto 80/15/5).

    No se genera aquí: la llena un job de analítica aparte (modelo de
    segmentación). Esta clase solo la lee.
    """

    __tablename__ = "segmentacion_productos"

    id_segmentacion = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    periodo_inicio = db.Column(db.Date, nullable=False)
    periodo_fin = db.Column(db.Date, nullable=False)
    ventas_periodo = db.Column(db.Numeric(10, 2), nullable=False)
    pct_acumulado = db.Column(db.Numeric(5, 2), nullable=False)
    clase_abc = db.Column(db.Enum("A", "B", "C", name="clase_abc"), nullable=False)
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    producto = db.relationship("Producto")

    def __repr__(self):
        return f"<SegmentacionProducto prod={self.id_producto} clase={self.clase_abc}>"
