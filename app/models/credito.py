from app.extensions import db


class Credito(db.Model):
    """
    Seguimiento de una venta al crédito (relación 1 a 1 con Venta). Se crea
    automáticamente al registrar una venta con tipo_venta='credito', con
    saldo_pendiente igual al total de la venta, y se va saldando mediante
    registros en Recuperacion. Si la venta se anula, el crédito pasa a 'anulado'.
    """

    __tablename__ = "creditos"
    __table_args__ = (
        db.Index("ix_creditos_estado", "estado"),
        # la base de datos misma impide un saldo negativo, aunque falle alguna validación de la aplicación
        db.CheckConstraint("saldo_pendiente >= 0", name="ck_creditos_saldo_no_negativo"),
        db.CheckConstraint("monto_credito >= 0", name="ck_creditos_monto_no_negativo"),
    )

    id_credito = db.Column(db.Integer, primary_key=True)
    id_venta = db.Column(db.Integer, db.ForeignKey("ventas.id_venta"), nullable=False)
    monto_credito = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_credito = db.Column(db.Date, nullable=False, server_default=db.text("(CURRENT_DATE)"))
    saldo_pendiente = db.Column(db.Numeric(10, 2), nullable=False)
    estado = db.Column(db.Enum("pendiente", "pagado", "anulado", name="estado_credito"), nullable=False, default="pendiente")

    venta = db.relationship("Venta", back_populates="credito")
    recuperaciones = db.relationship("Recuperacion", back_populates="credito", cascade="all, delete-orphan")

    # calcula si mostrar pendiente/parcial/pagado sin tener que guardar un estado nuevo en la bd
    @property
    def estado_visual(self):
        """
        Estado a mostrar en pantalla ('pendiente' | 'parcial' | 'pagado' | 'anulado').
        'parcial' es una etiqueta calculada, no un valor persistido: evita
        tocar el enum estado_credito por un matiz puramente visual.
        """
        if self.estado == "anulado":
            return "anulado"
        if self.estado == "pagado":
            return "pagado"
        if self.saldo_pendiente < self.monto_credito:
            return "parcial"
        return "pendiente"

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Credito venta={self.id_venta} saldo={self.saldo_pendiente}>"
