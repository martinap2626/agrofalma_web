from app.extensions import db


class RecomendacionAbastecimiento(db.Model):
    """
    Recomendación de reabastecimiento precalculada (HU-11, Sprint 3): cruza
    demanda prevista contra stock actual por sede, con un nivel de urgencia
    y un motivo en texto plano que explica el porqué -- esta tabla es la
    base de la "explicabilidad" del sistema: nunca se le pide al usuario
    que confíe en un número sin más, siempre se le dice de dónde sale.

    No se genera aquí: la llena un job de analítica aparte (Sprint 3,
    modelo de predicción + clasificación ABC). Esta clase solo la lee.
    """

    __tablename__ = "recomendaciones_abastecimiento"

    id_recomendacion = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    id_sede = db.Column(db.Integer, db.ForeignKey("sedes.id_sede"), nullable=False)
    stock_actual = db.Column(db.Integer, nullable=False)
    demanda_prevista = db.Column(db.Numeric(10, 2), nullable=False)
    cantidad_sugerida = db.Column(db.Integer, nullable=False, default=0)
    nivel_urgencia = db.Column(
        db.Enum("urgente", "media", "sin_accion", name="nivel_urgencia"), nullable=False
    )
    motivo = db.Column(db.String(150))
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    producto = db.relationship("Producto")
    sede = db.relationship("Sede")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<RecomendacionAbastecimiento prod={self.id_producto} sede={self.id_sede} urgencia={self.nivel_urgencia}>"
