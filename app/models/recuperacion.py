from app.extensions import db


class Recuperacion(db.Model):
    """Un pago (cobro) registrado contra un crédito pendiente."""

    __tablename__ = "recuperaciones"
    __table_args__ = (
        db.Index("ix_recuperaciones_fecha", "fecha_recuperacion"),
        db.Index("ix_recuperaciones_usuario_fecha", "id_usuario", "fecha_recuperacion"),
        db.CheckConstraint("monto_recuperado > 0", name="ck_recuperaciones_monto_positivo"),
    )

    id_recuperacion = db.Column(db.Integer, primary_key=True)
    id_credito = db.Column(db.Integer, db.ForeignKey("creditos.id_credito"), nullable=False)
    monto_recuperado = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_recuperacion = db.Column(db.Date, nullable=False, server_default=db.text("(CURRENT_DATE)"))
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False)
    # código único del formulario: evita registrar dos veces el mismo pago por un doble clic
    token_envio = db.Column(db.String(40), unique=True)

    credito = db.relationship("Credito", back_populates="recuperaciones")
    usuario = db.relationship("Usuario")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Recuperacion credito={self.id_credito} monto={self.monto_recuperado}>"
