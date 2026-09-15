"""
Modelos SQLAlchemy. Se importan aquí para que Flask-SQLAlchemy los registre
al llamar db.create_all() y para que otros módulos puedan hacer
`from app.models import Usuario, Producto, ...`.

Nota: las tablas de analítica (predicciones_demanda,
recomendaciones_abastecimiento, segmentacion_productos) ya existen en la
base de datos (ver agrofalma_bd.sql). Las tres tienen modelo ORM porque
las tres tienen pantalla propia (HU-09/10/11), pero ninguna se llena
desde la app: las llena un job de analítica aparte (modelo de predicción
de demanda + segmentación ABC), que todavía no está implementado -- por
ahora contienen datos de referencia para validar la interfaz.
"""

from app.models.rol import Rol
from app.models.sede import Sede
from app.models.usuario import Usuario
from app.models.categoria import Categoria
from app.models.unidad_medida import UnidadMedida
from app.models.producto import Producto
from app.models.stock_por_sede import StockPorSede
from app.models.cliente import Cliente
from app.models.venta import Venta
from app.models.detalle_venta import DetalleVenta
from app.models.credito import Credito
from app.models.recuperacion import Recuperacion
from app.models.recomendacion_abastecimiento import RecomendacionAbastecimiento
from app.models.prediccion_demanda import PrediccionDemanda
from app.models.segmentacion_producto import SegmentacionProducto

__all__ = [
    "Rol",
    "Sede",
    "Usuario",
    "Categoria",
    "UnidadMedida",
    "Producto",
    "StockPorSede",
    "Cliente",
    "Venta",
    "DetalleVenta",
    "Credito",
    "Recuperacion",
    "RecomendacionAbastecimiento",
    "PrediccionDemanda",
    "SegmentacionProducto",
]
