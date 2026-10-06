from app.extensions import db


class PrediccionDemanda(db.Model):
    """
    Predicción de demanda precalculada por producto base (HU-09).

    No se genera en la aplicación: la escribe el pipeline de analítica
    (`flask actualizar-analitica`, ver analitica_job/pipeline) y esta clase solo la
    lee. Cada ejecución agrega una fila por producto; la pantalla muestra la más
    reciente de cada uno, y las anteriores quedan como historial.

    Las columnas `periodo_objetivo`, `venta_mes_anterior`, `confiabilidad`, `metodo`
    y `error_validacion` las agrega `flask preparar-analitica` (la tabla original
    solo tenía horizonte, demanda, mape y tendencia).
    """

    __tablename__ = "predicciones_demanda"

    id_prediccion = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey("productos.id_producto"), nullable=False)
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())
    horizonte_semanas = db.Column(db.Integer, nullable=False)
    demanda_estimada = db.Column(db.Numeric(10, 2), nullable=False)
    mape = db.Column(db.Numeric(5, 2))
    tendencia_pct = db.Column(db.Numeric(5, 2))

    # --- agregadas por el pipeline real ---------------------------------------------
    periodo_objetivo = db.Column(db.Date)                  # mes que se pronostica (primer día)
    venta_mes_anterior = db.Column(db.Numeric(10, 2))      # unidades vendidas el último mes con datos
    confiabilidad = db.Column(db.String(10))               # Alta | Media | Baja (lenguaje de negocio)
    metodo = db.Column(db.String(30))                      # método de pronóstico usado
    error_validacion = db.Column(db.Numeric(6, 2))         # error % del método en la validación (WAPE del producto)

    producto = db.relationship("Producto")

    def __repr__(self):
        return f"<PrediccionDemanda prod={self.id_producto} demanda={self.demanda_estimada}>"
