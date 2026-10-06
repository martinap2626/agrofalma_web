from app.extensions import db


class PlanCampana(db.Model):
    """
    Plan de campaña por producto base (HU-09 ampliada): cuánto se espera vender en una campaña completa
    (grande: enero-junio; chica: julio-diciembre), cuánto va vendido y cuánto falta.

    Lo escribe el pipeline de analítica (`flask actualizar-analitica`) y la pantalla "Plan de campaña"
    solo lo lee. Cada ejecución reemplaza el plan anterior completo. Hay dos "tipos" de fila: la
    campaña en curso (con avance real) y la próxima (basada en la misma campaña del año anterior).
    """

    __tablename__ = "plan_campana"

    id_plan = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    campana_id = db.Column(db.String(8), nullable=False)             # "2026-C"
    campana_nombre = db.Column(db.String(80), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)                  # en_curso | proxima
    meses_conocidos = db.Column(db.Integer, nullable=False, default=0)
    vendido = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    referencia_anio_anterior = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    esperado_resto = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    esperado_total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    ritmo_global = db.Column(db.Numeric(6, 3))                       # ritmo de TODA la campaña (vendido hasta hoy / mismo tramo del año anterior)
    ritmo_pct = db.Column(db.Numeric(8, 1))                          # vendido hasta hoy vs. mismo tramo del año anterior
    clase_abc = db.Column(db.String(1))
    confianza = db.Column(db.String(10))
    error_tipico_pct = db.Column(db.Numeric(6, 1))
    stock_actual = db.Column(db.Integer)                             # NULL si no hay stock registrado
    por_cubrir = db.Column(db.Integer)                               # lo que falta vender menos el stock actual (>= 0)
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    producto = db.relationship("Producto")

    def __repr__(self):
        return f"<PlanCampana {self.campana_id} prod={self.id_producto} esperado={self.esperado_total}>"
