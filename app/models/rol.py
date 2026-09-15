from app.extensions import db


class Rol(db.Model):
    __tablename__ = "roles"

    id_rol = db.Column(db.Integer, primary_key=True)
    nombre_rol = db.Column(db.String(30), nullable=False, unique=True)
    descripcion = db.Column(db.String(100))
    estado = db.Column(db.Boolean, nullable=False, default=True)

    usuarios = db.relationship("Usuario", back_populates="rol")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Rol {self.nombre_rol}>"
