from app.extensions import db


class Recuperacion(db.Model):
    """Un pago (cobro) registrado contra un crédito pendiente."""

    __tablename__ = "recuperaciones"

    id_recuperacion = db.Column(db.Integer, primary_key=True)
    id_credito = db.Column(db.Integer, db.ForeignKey("creditos.id_credito"), nullable=False)
    monto_recuperado = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_recuperacion = db.Column(db.Date, nullable=False, server_default=db.func.current_date())
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False)

    credito = db.relationship("Credito", back_populates="recuperaciones")
    usuario = db.relationship("Usuario")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Recuperacion credito={self.id_credito} monto={self.monto_recuperado}>"
