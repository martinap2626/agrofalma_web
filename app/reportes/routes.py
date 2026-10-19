"""
Reportes comerciales (HU-12, Sprint 4, RF-12) — exclusivo del Administrador.

A diferencia del Dashboard (app/dashboard/routes.py), que resume un mes
calendario a la vez para lectura rápida, este módulo permite un rango de
fechas libre y entrega el detalle transaccional, pensado para sacarlo del
sistema (exportar a CSV) hacia el contador o una reunión.

Dos tipos de reporte por ahora: Ventas y Productos más vendidos. Ambos
comparten el mismo filtro de fechas/sede y la misma exportación CSV, sin
depender de librerías nuevas (csv es de la librería estándar).
"""

import csv
import io
from datetime import date
from decimal import Decimal

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
from app.models import Categoria, DetalleVenta, Producto, Sede, Venta
from app.seguridad import acciones_recientes, registrar_accion
from app.utilidades_fecha import rango_fechas

# filas del reporte de ventas que se muestran por página; el CSV exportado siempre incluye todas
VENTAS_POR_PAGINA = 50

bp = Blueprint("reportes", __name__, url_prefix="/reportes")


# pantalla de reportes: arma el reporte (ventas o productos) segun los filtros elegidos
@bp.route("")
@login_required
@rol_requerido("Administrador")
def inicio():
    sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()
    tipo, desde, hasta, id_sede = _leer_filtros(request.args)

    contexto = {
        "sedes": sedes, "tipo": tipo, "desde": desde, "hasta": hasta, "id_sede": id_sede,
        "ventas": [], "resumen_ventas": None, "productos": [], "resumen_productos": None,
        "pagina": 1, "total_paginas": 1,
    }

    try:
        if tipo == "productos":
            contexto["productos"], contexto["resumen_productos"] = _reporte_productos(desde, hasta, id_sede)
        else:
            contexto["ventas"], contexto["resumen_ventas"], contexto["pagina"], contexto["total_paginas"] = (
                _reporte_ventas(desde, hasta, id_sede, request.args.get("pagina", "1"))
            )
    except SQLAlchemyError as error:
        registrar_error_conexion("generar reporte comercial", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("reportes/inicio.html", **contexto), 503

    return render_template("reportes/inicio.html", **contexto)


# Excel interpreta como fórmula un texto que empieza con = + - @ (inyección de fórmulas en CSV): un nombre de cliente o de
# producto malicioso podría ejecutar algo al abrir el archivo. Se antepone una comilla para que se lea como texto.
def _texto_seguro(valor):
    texto = str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


# genera el mismo reporte pero como archivo csv para descargar
@bp.route("/exportar")
@login_required
@rol_requerido("Administrador")
def exportar():
    # tope de descargas por usuario: frena que una cuenta (o una sesión robada) copie los datos del negocio en masa
    try:
        if acciones_recientes(
            "reporte_exportado", current_user.id_usuario, current_app.config["EXPORT_VENTANA_MIN"]
        ) >= current_app.config["EXPORT_MAX_POR_VENTANA"]:
            registrar_accion("exportacion_limitada", "reporte", request.args.get("tipo", "ventas"),
                             "Se superó el máximo de descargas por ventana de tiempo", resultado="denegado")
            abort(429)
    except SQLAlchemyError as error:
        registrar_error_conexion("revisar el límite de exportaciones", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("reportes.inicio"))

    tipo, desde, hasta, id_sede = _leer_filtros(request.args)
    salida = io.StringIO()
    # ';' en vez de ',': en configuraciones regionales en español, Excel
    # usa el separador de lista de Windows (típicamente ';') al abrir un
    # CSV con doble clic, no el de este archivo — si no coinciden, Excel
    # mete todo en una sola columna aunque el CSV esté bien formado.
    escritor = csv.writer(salida, delimiter=";")

    try:
        if tipo == "productos":
            productos, _ = _reporte_productos(desde, hasta, id_sede)
            escritor.writerow(["Producto", "Categoría", "Cantidad vendida", "Total vendido (S/)"])
            for p in productos:
                escritor.writerow([_texto_seguro(p["producto"]), _texto_seguro(p["categoria"]), p["cantidad"], f'{p["total"]:.2f}'])
        else:
            ventas, _, _, _ = _reporte_ventas(desde, hasta, id_sede)
            escritor.writerow(["N° Operación", "Fecha", "Cliente", "Sede", "Tipo", "Total (S/)"])
            for v in ventas:
                escritor.writerow([
                    _texto_seguro(v.numero_operacion),
                    v.fecha_hora.strftime("%d/%m/%Y %H:%M"),
                    _texto_seguro(v.cliente.nombre_cliente),
                    _texto_seguro(v.sede.nombre_sede),
                    "Crédito" if v.es_credito else "Contado",
                    f"{v.total:.2f}",
                ])
    except SQLAlchemyError as error:
        registrar_error_conexion("exportar reporte comercial", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for(
            "reportes.inicio", tipo=tipo, desde=desde.isoformat(), hasta=hasta.isoformat(),
            id_sede=id_sede or "",
        ))

    nombre_archivo = f"reporte_{tipo}_{desde.isoformat()}_{hasta.isoformat()}.csv"
    # BOM al inicio: sin esto, Excel no detecta UTF-8 solo y muestra las
    # tildes/ñ corruptas. No se agrega una línea "sep=;": aunque es una
    # directiva válida de Excel, en la práctica hace que algunas versiones
    # ignoren el BOM y lean el resto del archivo con el codepage ANSI del
    # sistema en vez de UTF-8 -romper el acento en vez de arreglarlo-. El
    # delimitador ';' ya alcanza para que Excel en configuración regional
    # en español separe columnas correctamente sin ese aviso adicional.
    contenido = chr(0xFEFF) + salida.getvalue()
    registrar_accion("reporte_exportado", "reporte", tipo, f"{nombre_archivo}")
    return Response(
        contenido,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


# saca tipo/fechas/sede de los parametros de la url, con valores por defecto si faltan
def _leer_filtros(args):
    tipo = args.get("tipo", "ventas")
    if tipo not in ("ventas", "productos"):
        tipo = "ventas"

    hoy = date.today()
    try:
        desde = date.fromisoformat(args.get("desde", "").strip())
    except ValueError:
        desde = hoy.replace(day=1)
    try:
        hasta = date.fromisoformat(args.get("hasta", "").strip())
    except ValueError:
        hasta = hoy
    if hasta < desde:
        desde, hasta = hasta, desde

    id_sede_raw = args.get("id_sede", "").strip()
    id_sede = int(id_sede_raw) if id_sede_raw.isdigit() else None

    return tipo, desde, hasta, id_sede


# arma el listado y el resumen de ventas para el rango de fechas pedido; con pagina_raw=None trae todas las filas
# (exportación), con un valor trae solo esa página para que la pantalla no cargue miles de filas
def _reporte_ventas(desde, hasta, id_sede, pagina_raw=None):
    inicio, fin = rango_fechas(desde, hasta)
    filtro = [Venta.fecha_hora >= inicio, Venta.fecha_hora < fin, Venta.anulada.is_(False)]
    if id_sede:
        filtro.append(Venta.id_sede == id_sede)

    # el resumen lo calcula MySQL sobre TODAS las ventas del rango...
    por_tipo = {
        tipo: (int(n), Decimal(total or 0))
        for tipo, n, total in db.session.query(Venta.tipo_venta, db.func.count(), db.func.sum(Venta.total))
        .filter(*filtro).group_by(Venta.tipo_venta).all()
    }
    resumen = {
        "cantidad": sum(n for n, _ in por_tipo.values()),
        "total": sum((t for _, t in por_tipo.values()), Decimal("0")),
        "contado": por_tipo.get("contado", (0, Decimal("0")))[1],
        "credito": por_tipo.get("credito", (0, Decimal("0")))[1],
    }

    # ...y el detalle trae las filas con cliente y sede ya cargados
    consulta = (
        Venta.query.options(db.joinedload(Venta.cliente), db.joinedload(Venta.sede))
        .filter(*filtro).order_by(Venta.fecha_hora)
    )
    if pagina_raw is None:
        return consulta.all(), resumen, 1, 1
    # el total ya se conoce por el resumen: se evita un COUNT extra sobre el mismo rango
    pagina = max(int(pagina_raw), 1) if str(pagina_raw).isdigit() else 1
    total_paginas = max((resumen["cantidad"] + VENTAS_POR_PAGINA - 1) // VENTAS_POR_PAGINA, 1)
    pagina = min(pagina, total_paginas)
    return consulta.offset((pagina - 1) * VENTAS_POR_PAGINA).limit(VENTAS_POR_PAGINA).all(), resumen, pagina, total_paginas


# arma el listado y el resumen de productos mas vendidos para el rango de fechas pedido
def _reporte_productos(desde, hasta, id_sede):
    consulta = (
        db.session.query(
            Producto.nombre_producto, Categoria.nombre_categoria,
            db.func.sum(DetalleVenta.cantidad), db.func.sum(DetalleVenta.subtotal),
        )
        .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
        .join(Categoria, Categoria.id_categoria == Producto.id_categoria)
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .filter(Venta.fecha_hora >= rango_fechas(desde, hasta)[0], Venta.fecha_hora < rango_fechas(desde, hasta)[1],
                Venta.anulada.is_(False))
    )
    if id_sede:
        consulta = consulta.filter(Venta.id_sede == id_sede)
    filas = (
        consulta.group_by(Producto.id_producto)
        .order_by(db.func.sum(DetalleVenta.subtotal).desc())
        .all()
    )

    productos = [
        {"producto": nombre, "categoria": categoria, "cantidad": int(cantidad or 0), "total": float(total or 0)}
        for nombre, categoria, cantidad, total in filas
    ]
    resumen = {
        "cantidad_productos": len(productos),
        "total": sum((p["total"] for p in productos), 0.0),
    }
    return productos, resumen
