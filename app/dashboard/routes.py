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

from datetime import date
from decimal import Decimal

from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
from app.models import (
    Categoria, Credito, DetalleVenta, Producto, RecomendacionAbastecimiento, Recuperacion, Sede,
    StockPorSede, Usuario, Venta,
)

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


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
    except ValueError:
        anio, mes = hoy.year, hoy.month

    valores_vacios = {
        "ventas_contado": Decimal("0"), "ventas_credito": Decimal("0"), "total_mes": Decimal("0"),
        "recuperaciones_mes": Decimal("0"), "ingreso_caja_mes": Decimal("0"),
        "ventas_por_categoria": [], "ventas_por_trabajador": [],
        "stock_bajo": [], "top_productos": [], "alertas_urgentes": [],
    }

    try:
        consulta_ventas = Venta.query.filter(
            db.extract("year", Venta.fecha_hora) == anio,
            db.extract("month", Venta.fecha_hora) == mes,
        )
        if id_sede:
            consulta_ventas = consulta_ventas.filter(Venta.id_sede == id_sede)
        ventas_mes = consulta_ventas.all()

        ventas_contado = sum((v.total for v in ventas_mes if v.tipo_venta == "contado"), Decimal("0"))
        ventas_credito = sum((v.total for v in ventas_mes if v.tipo_venta == "credito"), Decimal("0"))

        consulta_recuperaciones = Recuperacion.query.join(Credito).join(Venta).filter(
            db.extract("year", Recuperacion.fecha_recuperacion) == anio,
            db.extract("month", Recuperacion.fecha_recuperacion) == mes,
        )
        if id_sede:
            consulta_recuperaciones = consulta_recuperaciones.filter(Venta.id_sede == id_sede)
        recuperaciones_mes = sum(
            (r.monto_recuperado for r in consulta_recuperaciones.all()), Decimal("0")
        )

        consulta_categoria = (
            db.session.query(Categoria.nombre_categoria, db.func.sum(DetalleVenta.subtotal))
            .join(Producto, Producto.id_categoria == Categoria.id_categoria)
            .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
            .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
            .filter(
                db.extract("year", Venta.fecha_hora) == anio,
                db.extract("month", Venta.fecha_hora) == mes,
            )
        )
        if id_sede:
            consulta_categoria = consulta_categoria.filter(Venta.id_sede == id_sede)
        filas_categoria = consulta_categoria.group_by(Categoria.nombre_categoria).all()

        consulta_trabajador = (
            db.session.query(Usuario.nombre, Usuario.apellido, db.func.sum(Venta.total))
            .join(Venta, Venta.id_usuario == Usuario.id_usuario)
            .filter(
                db.extract("year", Venta.fecha_hora) == anio,
                db.extract("month", Venta.fecha_hora) == mes,
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
                db.extract("year", Venta.fecha_hora) == anio,
                db.extract("month", Venta.fecha_hora) == mes,
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

    return render_template(
        "dashboard/inicio.html",
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
