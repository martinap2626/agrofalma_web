from app.extensions import db


class Categoria(db.Model):
    __tablename__ = "categorias"

    id_categoria = db.Column(db.Integer, primary_key=True)
    nombre_categoria = db.Column(db.String(40), nullable=False, unique=True)
    estado = db.Column(db.Boolean, nullable=False, default=True)

    productos = db.relationship("Producto", back_populates="categoria")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Categoria {self.nombre_categoria}>"
