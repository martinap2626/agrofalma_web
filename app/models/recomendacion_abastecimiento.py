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
    stock_actual = db.Column(db.Integer)  # NULL = todavía no hay inventario de este producto (modo "plan")
    demanda_prevista = db.Column(db.Numeric(10, 2), nullable=False)
    cantidad_sugerida = db.Column(db.Integer, nullable=False, default=0)
    # urgente / media / sin_accion cuando hay stock; "plan" cuando no hay inventario y solo se indica cuánto tener
    nivel_urgencia = db.Column(
        db.Enum("urgente", "media", "sin_accion", "plan", name="nivel_urgencia"), nullable=False
    )
    motivo = db.Column(db.String(400))
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    # --- agregadas por el pipeline real (flask preparar-analitica) -----------------------
    clase_abc = db.Column(db.String(1))
    stock_objetivo = db.Column(db.Integer)      # hasta cuánto conviene tener
    stock_seguridad = db.Column(db.Integer)     # margen de seguridad incluido en el objetivo
    punto_pedido = db.Column(db.Integer)        # por debajo de esto conviene pedir
    confiabilidad = db.Column(db.String(10))    # confiabilidad del pronóstico de este producto
    reparto = db.Column(db.String(250))         # cómo dividir lo sugerido entre presentaciones
    con_inventario = db.Column(db.Boolean, nullable=False, default=False)

    producto = db.relationship("Producto")
    sede = db.relationship("Sede")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<RecomendacionAbastecimiento prod={self.id_producto} sede={self.id_sede} urgencia={self.nivel_urgencia}>"
