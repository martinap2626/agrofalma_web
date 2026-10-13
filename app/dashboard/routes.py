"""
Dashboard comercial del Administrador (HU-07, Sprint 2, RF-08).

El dashboard del Trabajador no vive aquí: reutiliza ventas.mis_ventas,
que ya muestra su actividad diaria (ver app/ventas/routes.py). Esta
vista es exclusiva del rol Administrador -RNF-03-, con una mirada
agregada por sede y por mes, que es el nivel de detalle que necesita
el gerente para decidir, no el que necesita un vendedor en el día a día.

El filtro de sede es intencional y no cosmético: el problema central de
la tesis es el desbalance de ventas/stock entre Pimentel y Chacupe, así
que el dashboard debe poder aislar una sede a la vez, no solo mostrar
el consolidado.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.utilidades_fecha import rango_mes
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
from app.models import (
    Bitacora, Categoria, Credito, DetalleVenta, EventoNegocio, PrediccionDemanda, Producto, RecomendacionAbastecimiento,
    Recuperacion, SegmentacionProducto, Sede, StockPorSede, Usuario, Venta,
)

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

# a partir de cuántos hechos sospechosos en 24 horas el dashboard muestra una alerta
UMBRAL_ALERTA_ACCESOS = 5


# cuenta en la bitácora lo que el Administrador debe vigilar: accesos fallidos y bloqueos (últimas 24 h) y
# anulaciones, rebajas de precio y conteos de inventario (últimos 7 días)
def _resumen_control():
    ahora = datetime.now()

    def contar(desde):
        filas = (
            db.session.query(Bitacora.accion, db.func.count())
            .filter(Bitacora.fecha_hora > desde).group_by(Bitacora.accion).all()
        )
        return dict(filas)

    dia, semana = contar(ahora - timedelta(hours=24)), contar(ahora - timedelta(days=7))

    def suma(conteo, acciones):
        return sum(conteo.get(a, 0) for a in acciones)

    control = {
        "accesos_fallidos": suma(dia, ("login_fallido", "login_cuenta_inactiva")),
        "bloqueos": suma(dia, ("login_bloqueado",)),
        "denegados": suma(dia, ("acceso_denegado", "csrf_rechazado", "exportacion_limitada")),
        "anulaciones": suma(semana, ("venta_anulada",)),
        "rebajas": suma(semana, ("venta_precio_rebajado",)),
        "conteos": suma(semana, ("inventario_conteo",)),
    }
    control["alerta"] = (
        control["accesos_fallidos"] >= UMBRAL_ALERTA_ACCESOS or control["bloqueos"] > 0
        or control["denegados"] >= UMBRAL_ALERTA_ACCESOS
    )
    return control


# arma todos los numeros del dashboard para el mes y la sede que se elijan
@bp.route("")
@login_required
@rol_requerido("Administrador")
def inicio():
    sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()

    id_sede_raw = request.args.get("id_sede", "").strip()
    id_sede = int(id_sede_raw) if id_sede_raw.isdigit() else None

    hoy = date.today()
    try:
        anio, mes = (int(parte) for parte in request.args.get("mes", "").split("-"))
        if not (1 <= mes <= 12 and 2000 <= anio <= 2100):
            raise ValueError  # mes o año imposibles: se usa el mes actual
    except ValueError:
        anio, mes = hoy.year, hoy.month

    valores_vacios = {
        "ventas_contado": Decimal("0"), "ventas_credito": Decimal("0"), "total_mes": Decimal("0"),
        "recuperaciones_mes": Decimal("0"), "ingreso_caja_mes": Decimal("0"),
        "ventas_por_categoria": [], "ventas_por_trabajador": [],
        "stock_bajo": [], "top_productos": [], "alertas_urgentes": [],
    }

    try:
        # rango [inicio, fin) del mes: sargable, aprovecha el índice por fecha
        inicio_mes, fin_mes = rango_mes(anio, mes)

        # los totales los suma MySQL (una fila por tipo de venta), no Python sobre miles de filas
        consulta_ventas = db.session.query(Venta.tipo_venta, db.func.sum(Venta.total)).filter(
            Venta.fecha_hora >= inicio_mes, Venta.fecha_hora < fin_mes, Venta.anulada.is_(False),
        )
        if id_sede:
            consulta_ventas = consulta_ventas.filter(Venta.id_sede == id_sede)
        totales_por_tipo = dict(consulta_ventas.group_by(Venta.tipo_venta).all())

        ventas_contado = Decimal(totales_por_tipo.get("contado") or 0)
        ventas_credito = Decimal(totales_por_tipo.get("credito") or 0)

        consulta_recuperaciones = (
            db.session.query(db.func.coalesce(db.func.sum(Recuperacion.monto_recuperado), 0))
            .join(Credito, Credito.id_credito == Recuperacion.id_credito)
            .join(Venta, Venta.id_venta == Credito.id_venta)
            .filter(
                Recuperacion.fecha_recuperacion >= inicio_mes.date(),
                Recuperacion.fecha_recuperacion < fin_mes.date(),
            )
        )
        if id_sede:
            consulta_recuperaciones = consulta_recuperaciones.filter(Venta.id_sede == id_sede)
        recuperaciones_mes = Decimal(consulta_recuperaciones.scalar() or 0)

        consulta_categoria = (
            db.session.query(Categoria.nombre_categoria, db.func.sum(DetalleVenta.subtotal))
            .join(Producto, Producto.id_categoria == Categoria.id_categoria)
            .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
            .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
            .filter(
                Venta.fecha_hora >= inicio_mes,
                Venta.fecha_hora < fin_mes, Venta.anulada.is_(False),
            )
        )
        if id_sede:
            consulta_categoria = consulta_categoria.filter(Venta.id_sede == id_sede)
        filas_categoria = consulta_categoria.group_by(Categoria.nombre_categoria).all()

        consulta_trabajador = (
            db.session.query(Usuario.nombre, Usuario.apellido, db.func.sum(Venta.total))
            .join(Venta, Venta.id_usuario == Usuario.id_usuario)
            .filter(
                Venta.fecha_hora >= inicio_mes,
                Venta.fecha_hora < fin_mes, Venta.anulada.is_(False),
            )
        )
        if id_sede:
            consulta_trabajador = consulta_trabajador.filter(Venta.id_sede == id_sede)
        filas_trabajador = (
            consulta_trabajador.group_by(Usuario.id_usuario)
            .order_by(db.func.sum(Venta.total).desc())
            .all()
        )

        # Alertas proactivas: lo mas urgente de recomendaciones_abastecimiento,
        # para que el administrador lo vea apenas entra, sin tener que ir a
        # buscarlo a la pantalla de Analitica. Igual que stock bajo, es una
        # foto del momento, no depende del mes filtrado.
        consulta_alertas = RecomendacionAbastecimiento.query.options(
            db.joinedload(RecomendacionAbastecimiento.producto),
            db.joinedload(RecomendacionAbastecimiento.sede),
        ).filter(RecomendacionAbastecimiento.nivel_urgencia == "urgente")
        if id_sede:
            consulta_alertas = consulta_alertas.filter(RecomendacionAbastecimiento.id_sede == id_sede)
        filas_alertas = consulta_alertas.order_by(RecomendacionAbastecimiento.demanda_prevista.desc()).all()

        # Stock bajo mínimo: es una foto del inventario actual, no depende
        # del mes filtrado (stock_por_sede no tiene rango de fechas propio).
        consulta_stock_bajo = (
            db.session.query(
                Producto.nombre_producto, Sede.nombre_sede,
                StockPorSede.cantidad_actual, Producto.stock_minimo,
            )
            .join(StockPorSede, StockPorSede.id_producto == Producto.id_producto)
            .join(Sede, Sede.id_sede == StockPorSede.id_sede)
            .filter(StockPorSede.cantidad_actual < Producto.stock_minimo, Producto.estado.is_(True))
        )
        if id_sede:
            consulta_stock_bajo = consulta_stock_bajo.filter(StockPorSede.id_sede == id_sede)
        filas_stock_bajo = consulta_stock_bajo.order_by(StockPorSede.cantidad_actual.asc()).all()

        # Top 5 productos más vendidos: sí respeta mes y sede, porque mide
        # actividad de venta del periodo, no inventario actual.
        consulta_top_productos = (
            db.session.query(Producto.nombre_producto, db.func.sum(DetalleVenta.subtotal))
            .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
            .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
            .filter(
                Venta.fecha_hora >= inicio_mes,
                Venta.fecha_hora < fin_mes, Venta.anulada.is_(False),
            )
        )
        if id_sede:
            consulta_top_productos = consulta_top_productos.filter(Venta.id_sede == id_sede)
        filas_top_productos = (
            consulta_top_productos.group_by(Producto.id_producto)
            .order_by(db.func.sum(DetalleVenta.subtotal).desc())
            .limit(5)
            .all()
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("cargar dashboard comercial", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "dashboard/inicio.html", sedes=sedes, id_sede=id_sede, anio=anio, mes=mes, **valores_vacios
        ), 503

    # control y seguridad (intentos de acceso fallidos, anulaciones, rebajas de precio...): se omite sin romper el dashboard si falla
    control = None
    try:
        control = _resumen_control()
    except SQLAlchemyError as error:
        registrar_error_conexion("cargar el resumen de control del dashboard", error)

    # panorama de la analítica (pronóstico, clases ABC, reposición y eventos): se omite sin romper el dashboard si falla
    panorama = None
    try:
        clases = dict(db.session.query(SegmentacionProducto.clase_abc, db.func.count()).group_by(SegmentacionProducto.clase_abc).all())
        niveles = dict(db.session.query(RecomendacionAbastecimiento.nivel_urgencia, db.func.count())
                       .group_by(RecomendacionAbastecimiento.nivel_urgencia).all())
        total_pronostico = db.session.query(db.func.sum(RecomendacionAbastecimiento.demanda_prevista)).scalar()
        periodo = db.session.query(db.func.max(PrediccionDemanda.periodo_objetivo)).scalar()
        top = (RecomendacionAbastecimiento.query.join(Producto).options(db.joinedload(RecomendacionAbastecimiento.producto))
               .order_by(RecomendacionAbastecimiento.demanda_prevista.desc()).limit(5).all())
        eventos = (EventoNegocio.query.options(db.joinedload(EventoNegocio.producto))
                   .order_by(EventoNegocio.fecha_inicio.desc()).limit(5).all())
        if clases or niveles:
            panorama = {
                "periodo": periodo, "pronostico_total": float(total_pronostico or 0), "clases": clases,
                "urgentes": niveles.get("urgente", 0), "medias": niveles.get("media", 0),
                "top": [(r.producto.nombre_producto, float(r.demanda_prevista), r.clase_abc) for r in top],
                "eventos": [(e.fecha_inicio, e.producto.nombre_producto if e.producto else "Todo el negocio", ("[Ejemplo] " if e.es_ejemplo else "") + e.descripcion) for e in eventos],
            }
    except SQLAlchemyError as error:
        registrar_error_conexion("cargar panorama analitico del dashboard", error)
        db.session.rollback()

    return render_template(
        "dashboard/inicio.html",
        control=control,
        panorama=panorama,
        sedes=sedes,
        id_sede=id_sede,
        anio=anio,
        mes=mes,
        ventas_contado=ventas_contado,
        ventas_credito=ventas_credito,
        total_mes=ventas_contado + ventas_credito,
        recuperaciones_mes=recuperaciones_mes,
        ingreso_caja_mes=ventas_contado + recuperaciones_mes,
        ventas_por_categoria=[
            {"categoria": nombre, "total": float(total or 0)} for nombre, total in filas_categoria
        ],
        ventas_por_trabajador=[
            {"nombre": f"{nombre} {apellido}", "total": float(total or 0)}
            for nombre, apellido, total in filas_trabajador
        ],
        stock_bajo=[
            {"producto": nombre, "sede": sede, "actual": actual, "minimo": minimo}
            for nombre, sede, actual, minimo in filas_stock_bajo
        ],
        top_productos=[
            {"producto": nombre, "total": float(total or 0)} for nombre, total in filas_top_productos
        ],
        alertas_urgentes=[
            {
                "producto": a.producto.nombre_producto,
                "sede": a.sede.nombre_sede,
                "motivo": a.motivo,
                "cantidad_sugerida": a.cantidad_sugerida,
            }
            for a in filas_alertas
        ],
    )
