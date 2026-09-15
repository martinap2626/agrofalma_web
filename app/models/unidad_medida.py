from app.extensions import db


class UnidadMedida(db.Model):
    __tablename__ = "unidades_medida"

    id_unidad = db.Column(db.Integer, primary_key=True)
    nombre_unidad = db.Column(db.String(20), nullable=False, unique=True)
    abreviatura = db.Column(db.String(5), nullable=False)

    productos = db.relationship("Producto", back_populates="unidad")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<UnidadMedida {self.nombre_unidad}>"
