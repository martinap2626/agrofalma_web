from app.extensions import db


class Cliente(db.Model):
    __tablename__ = "clientes"
    # el documento se busca al crear o editar un cliente (para no duplicarlo)
    __table_args__ = (db.Index("ix_clientes_documento", "documento"),)

    id_cliente = db.Column(db.Integer, primary_key=True)
    nombre_cliente = db.Column(db.String(100), nullable=False)
    documento = db.Column(db.String(15))
    telefono = db.Column(db.String(15))
    estado = db.Column(db.Boolean, nullable=False, default=True)

    ventas = db.relationship("Venta", back_populates="cliente")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Cliente {self.nombre_cliente}>"
