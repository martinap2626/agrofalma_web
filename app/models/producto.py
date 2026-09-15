from app.extensions import db


class Producto(db.Model):
    """
    Catálogo de productos (RF-03: gestión del catálogo, RF-07: consulta
    del catálogo). El stock se consulta desde stock_por_sede — nunca se
    guarda un total en esta tabla, porque el problema que resuelve la
    tesis es justamente que el stock se descoordina entre sedes.
    """

    __tablename__ = "productos"

    id_producto = db.Column(db.Integer, primary_key=True)
    codigo_producto = db.Column(db.String(20), nullable=False, unique=True)
    nombre_producto = db.Column(db.String(80), nullable=False)
    id_categoria = db.Column(db.Integer, db.ForeignKey("categorias.id_categoria"), nullable=False)
    id_unidad = db.Column(db.Integer, db.ForeignKey("unidades_medida.id_unidad"), nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    stock_minimo = db.Column(db.Integer, nullable=False, default=0)
    descripcion = db.Column(db.String(200))
    estado = db.Column(db.Boolean, nullable=False, default=True)
    fecha_registro = db.Column(db.DateTime, server_default=db.func.now())

    categoria = db.relationship("Categoria", back_populates="productos")
    unidad = db.relationship("UnidadMedida", back_populates="productos")
    stock_por_sede = db.relationship(
        "StockPorSede", back_populates="producto", cascade="all, delete-orphan"
    )

    # suma el stock de todas las sedes para este producto
    @property
    def stock_total(self):
        """Suma del stock del producto en todas las sedes."""
        return sum(s.cantidad_actual for s in self.stock_por_sede)

    # como se ve el objeto en consola/logs, solo para debug
    def __repr__(self):
        return f"<Producto {self.codigo_producto} - {self.nombre_producto}>"
