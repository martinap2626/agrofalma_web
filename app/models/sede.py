from app.extensions import db


class Sede(db.Model):
    __tablename__ = "sedes"

    id_sede = db.Column(db.Integer, primary_key=True)
    nombre_sede = db.Column(db.String(50), nullable=False, unique=True)
    direccion = db.Column(db.String(150))
    telefono = db.Column(db.String(15))
    estado = db.Column(db.Boolean, nullable=False, default=True)
    fecha_creacion = db.Column(db.DateTime, server_default=db.func.now())

    usuarios = db.relationship("Usuario", back_populates="sede")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Sede {self.nombre_sede}>"
