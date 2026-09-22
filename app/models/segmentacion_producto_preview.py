from app.extensions import db


class SegmentacionProductoPreview(db.Model):
    """
    Vista previa de segmentación ABC (HU-10) con datos reales del histórico
    de ventas (Excel), mientras la tabla segmentacion_productos (que
    referencia id_producto) sigue bloqueada por el catálogo de productos
    de prueba en MySQL -- ver memoria del proyecto, "preguntas pendientes
    para el gerente".

    Identifica el producto por nombre normalizado (texto), no por
    id_producto: es intencional y temporal, no el diseño final.
    """

    __tablename__ = "segmentacion_productos_preview"

    id_preview = db.Column(db.Integer, primary_key=True)
    producto_normalizado = db.Column(db.String(120), nullable=False)
    categoria = db.Column(db.String(40), nullable=False)
    periodo_inicio = db.Column(db.String(7), nullable=False)  # "YYYY-MM"
    periodo_fin = db.Column(db.String(7), nullable=False)
    ventas_periodo = db.Column(db.Numeric(12, 2), nullable=False)
    pct_acumulado = db.Column(db.Numeric(5, 2), nullable=False)
    clase_abc = db.Column(db.Enum("A", "B", "C", name="clase_abc_preview"), nullable=False)
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<SegmentacionProductoPreview {self.producto_normalizado} clase={self.clase_abc}>"
