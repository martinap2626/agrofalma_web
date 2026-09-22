from app.extensions import db


class PrediccionDemandaPreview(db.Model):
    """
    Vista previa de predicción de demanda (HU-09) con datos reales del
    histórico de ventas (Excel), mientras la tabla predicciones_demanda
    (que referencia id_producto) sigue bloqueada por el catálogo de
    productos de prueba en MySQL -- ver memoria del proyecto, "preguntas
    pendientes para el gerente".

    Identifica el producto por nombre normalizado (texto), no por
    id_producto: es intencional y temporal, no el diseño final.
    """

    __tablename__ = "predicciones_demanda_preview"

    id_preview = db.Column(db.Integer, primary_key=True)
    producto_normalizado = db.Column(db.String(120), nullable=False)
    categoria = db.Column(db.String(40), nullable=False)
    periodo_objetivo = db.Column(db.String(7), nullable=False)  # "YYYY-MM"
    demanda_estimada = db.Column(db.Numeric(10, 2), nullable=False)
    mape_modelo = db.Column(db.Numeric(5, 2))
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<PrediccionDemandaPreview {self.producto_normalizado} -> {self.demanda_estimada}>"
