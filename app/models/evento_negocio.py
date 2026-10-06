from app.extensions import db

# tipos de evento que el gerente puede registrar; el segundo valor es el texto que ve el usuario
TIPOS_EVENTO = {
    "quiebre_stock": "Quiebre de stock (se dejó de vender por falta)",
    "produccion": "Problema o cambio de producción propia",
    "clima_plaga": "Clima o plaga que cambió la demanda",
    "promocion": "Promoción o descuento",
    "precio": "Cambio de precio",
    "competencia": "Competencia o cliente grande",
    "otro": "Otro hecho relevante",
}
IMPACTOS = {"alto": "Alto", "medio": "Medio", "bajo": "Bajo"}


class EventoNegocio(db.Model):
    """
    Hechos del negocio que explican las ventas de un mes, una campaña o un año (por ejemplo, un quiebre de
    stock, una plaga o una promoción). Los Excel solo guardan lo que se vendió; esto guarda POR QUÉ.

    Si el evento es un quiebre de stock y se anota la cantidad que no se pudo vender, el pipeline de analítica
    la suma a la demanda de ese mes (la venta perdida también era demanda). Los demás eventos se muestran
    junto a los resultados para que el gerente interprete los meses atípicos.
    """

    __tablename__ = "eventos_negocio"
    __table_args__ = (db.Index("ix_eventos_fecha", "fecha_inicio"),)

    id_evento = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto", ondelete="SET NULL"))  # vacío = afecta a todo el negocio
    id_sede = db.Column(db.Integer, db.ForeignKey("sedes.id_sede"))
    descripcion = db.Column(db.String(200), nullable=False)
    impacto = db.Column(db.String(6), nullable=False, default="medio")
    cantidad_no_atendida = db.Column(db.Integer)  # solo para quiebre de stock: unidades que no se pudieron vender
    es_ejemplo = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text("0"))  # solo para visualizar: NO afecta los cálculos
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario", ondelete="SET NULL"))
    fecha_registro = db.Column(db.DateTime, server_default=db.func.now())

    producto = db.relationship("Producto")
    sede = db.relationship("Sede")
    usuario = db.relationship("Usuario")

    @property
    def campana(self):
        """Campaña agrícola a la que pertenece la fecha de inicio: grande (ene-jun) o chica (jul-dic)."""
        return f"{'grande' if self.fecha_inicio.month <= 6 else 'chica'} {self.fecha_inicio.year}"

    def __repr__(self):
        return f"<EventoNegocio {self.tipo} {self.fecha_inicio}>"
