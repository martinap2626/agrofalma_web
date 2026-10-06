from app.extensions import db


class EjecucionAnalitica(db.Model):
    """
    Registro de cada corrida del pipeline de analítica: cuándo se calculó, con qué
    datos y qué método quedó elegido por clase. Sirve para mostrar en pantalla "calculado
    el ... con datos hasta ..." y como evidencia de trazabilidad del modelo.
    """

    __tablename__ = "ejecuciones_analitica"

    id_ejecucion = db.Column(db.Integer, primary_key=True)
    fecha_ejecucion = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    datos_hasta = db.Column(db.String(7), nullable=False)          # "2026-08"
    entrenado_desde = db.Column(db.Integer, nullable=False)        # año desde el que se usaron datos
    productos = db.Column(db.Integer, nullable=False)
    metodo_por_clase = db.Column(db.String(120))                   # {"A": "mezcla", ...}
    duracion_segundos = db.Column(db.Numeric(6, 1))
    resumen = db.Column(db.Text)                                   # JSON con métricas y alertas de calidad de datos

    def __repr__(self):
        return f"<EjecucionAnalitica {self.fecha_ejecucion} hasta {self.datos_hasta}>"
