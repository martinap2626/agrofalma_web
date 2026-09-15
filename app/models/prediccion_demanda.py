from app.extensions import db


class PrediccionDemanda(db.Model):
    """
    Predicción de demanda precalculada por producto (HU-09, Sprint 3).

    No se genera aquí: la llena un job de analítica aparte (modelo de
    predicción de demanda). Esta clase solo la lee. Mientras ese modelo no
    esté implementado, la tabla contiene valores de referencia (incluido
    `mape`) para probar la interfaz y su visualización -- no representan
    el resultado real de evaluación del objetivo específico 1 de la tesis.
    """

    __tablename__ = "predicciones_demanda"

    id_prediccion = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())
    horizonte_semanas = db.Column(db.Integer, nullable=False)
    demanda_estimada = db.Column(db.Numeric(10, 2), nullable=False)
    mape = db.Column(db.Numeric(5, 2))
    tendencia_pct = db.Column(db.Numeric(5, 2))

    producto = db.relationship("Producto")

    def __repr__(self):
        return f"<PrediccionDemanda prod={self.id_producto} demanda={self.demanda_estimada}>"
