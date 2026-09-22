from app.extensions import db


class PrediccionDemandaPreview(db.Model):
    """
    Vista previa de predicción de demanda (HU-09) con datos reales del
    histórico de ventas (Excel), mientras la tabla predicciones_demanda
    (que referencia id_producto) sigue bloqueada por el catálogo de
    productos de prueba en MySQL -- ver memoria del proyecto, "preguntas
    pendientes para el gerente".

    Identifica el producto por nombre normalizado (texto), no por
    id_producto: es intencional y temporal, no el diseño final.
    """

    __tablename__ = "predicciones_demanda_preview"

    id_preview = db.Column(db.Integer, primary_key=True)
    producto_normalizado = db.Column(db.String(120), nullable=False)
    categoria = db.Column(db.String(40), nullable=False)
    clase_abc = db.Column(db.String(20))  # "A", "B", "C" o "Sin clasificar" -- prioridad de negocio
    periodo_objetivo = db.Column(db.String(7), nullable=False)  # "YYYY-MM"
    # cantidad a reponer, ya redondeada hacia arriba (no tiene sentido comprar
    # media unidad, y para stock siempre conviene redondear a favor de no
    # quedarse corto) -- se muestra al administrador como numero accionable,
    # no como una demanda estadistica cruda
    cantidad_sugerida = db.Column(db.Integer, nullable=False)
    venta_mes_anterior = db.Column(db.Numeric(10, 2))  # para mostrar tendencia (sube/baja/sin historial)
    # Alta / Media / Baja -- traduccion en lenguaje de negocio del MASE por
    # clase ABC (Mean Absolute Scaled Error, Hyndman 2006; MASE<1 = mejor que
    # el metodo ingenuo). El numero de MASE nunca se le muestra al
    # administrador -- ver Documentacion_Avance80.docx sobre por que se
    # prefiere MASE al MAPE, y la decision de traducirlo a badges en vez de
    # mostrar el numero.
    confiabilidad = db.Column(db.String(10))
    fecha_generacion = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<PrediccionDemandaPreview {self.producto_normalizado} -> {self.cantidad_sugerida}>"
