from app.extensions import db


class Venta(db.Model):
    """
    Cabecera de una venta (HU del Sprint 2). Una vez registrada no se edita
    ni se elimina: es un comprobante, igual que en el mundo real. El detalle
    de productos vendidos vive en DetalleVenta y, si es al crédito, su
    seguimiento vive en Credito (relación 1 a 1).
    """

    __tablename__ = "ventas"
    # índices por fecha: casi todas las pantallas filtran ventas por rango de
    # fechas (dashboard, reportes, mis ventas, consolidado)
    __table_args__ = (
        db.Index("ix_ventas_fecha", "fecha_hora"),
        db.Index("ix_ventas_usuario_fecha", "id_usuario", "fecha_hora"),
        db.Index("ix_ventas_sede_fecha", "id_sede", "fecha_hora"),
    )

    id_venta = db.Column(db.Integer, primary_key=True)
    numero_operacion = db.Column(db.String(15), nullable=False, unique=True)
    fecha_hora = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    id_cliente = db.Column(db.Integer, db.ForeignKey("clientes.id_cliente"), nullable=False)
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False)
    id_sede = db.Column(db.Integer, db.ForeignKey("sedes.id_sede"), nullable=False)
    tipo_venta = db.Column(db.Enum("contado", "credito", name="tipo_venta"), nullable=False, default="contado")
    estado_pago = db.Column(db.Enum("pagado", "pendiente", name="estado_pago"), nullable=False, default="pagado")
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    observaciones = db.Column(db.String(150))

    cliente = db.relationship("Cliente", back_populates="ventas")
    usuario = db.relationship("Usuario")
    sede = db.relationship("Sede")
    detalle = db.relationship("DetalleVenta", back_populates="venta", cascade="all, delete-orphan")
    credito = db.relationship("Credito", back_populates="venta", uselist=False, cascade="all, delete-orphan")

    # true si esta venta fue al credito, para no andar comparando el string en todos lados
    @property
    def es_credito(self):
        return self.tipo_venta == "credito"

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Venta {self.numero_operacion}>"
