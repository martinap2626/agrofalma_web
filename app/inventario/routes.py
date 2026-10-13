"""
Módulo de inventario (solo Administrador): ver cuánto stock hay de cada producto en cada sede, registrar ingresos de
mercadería (compra o producción propia), corregir el stock con un conteo físico y consultar el kárdex (historial de
movimientos de cada producto).

Antes de este módulo el stock solo bajaba con las ventas y la única forma de cargarlo era un comando de consola.
Ahora todo cambio de stock (venta, anulación, ingreso, conteo) queda registrado con quién, cuándo, cuánto y por qué.
"""

import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, es_clave_repetida, registrar_error_conexion
from app.extensions import db
from app.models import MovimientoInventario, Producto, Sede, StockPorSede
from app.models.movimiento_inventario import TIPOS_MOVIMIENTO
from app.paginacion import paginar
from app.seguridad import registrar_accion, validar_largo
from app.servicios_inventario import CANTIDAD_MAXIMA, ajustar_a_conteo, ingresar

bp = Blueprint("inventario", __name__, url_prefix="/inventario")

PRODUCTOS_POR_PAGINA = 25
MOVIMIENTOS_A_MOSTRAR = 100

# motivos habituales de un ingreso; el usuario puede añadir un detalle (número de factura, lote...)
MOTIVOS_INGRESO = {
    "compra": "Compra a proveedor",
    "produccion": "Producción propia",
    "devolucion": "Devolución de un cliente",
    "otro": "Otro motivo",
}


# sede elegida en la pantalla: la de la URL si es válida, y si no la del usuario
def _leer_sede(sedes, id_sede_raw):
    ids = {s.id_sede for s in sedes}
    if id_sede_raw and id_sede_raw.isdigit() and int(id_sede_raw) in ids:
        return int(id_sede_raw)
    return current_user.id_sede if current_user.id_sede in ids else (sedes[0].id_sede if sedes else None)


# lista de productos con su stock en la sede elegida (el Administrador ve de un vistazo qué falta y qué es solo estimado)
@bp.route("")
@login_required
@rol_requerido("Administrador")
def listar():
    busqueda = request.args.get("q", "").strip()
    solo_alerta = request.args.get("alerta") == "1"
    contexto_vacio = {
        "filas": [], "sedes": [], "id_sede": None, "busqueda": busqueda, "solo_alerta": solo_alerta,
        "pagina": 1, "total_paginas": 1, "total": 0, "provisionales": 0,
    }

    try:
        sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()
        id_sede = _leer_sede(sedes, request.args.get("id_sede", "").strip())
        consulta = (
            db.session.query(Producto, StockPorSede)
            .outerjoin(StockPorSede, db.and_(StockPorSede.id_producto == Producto.id_producto, StockPorSede.id_sede == id_sede))
            .options(db.joinedload(Producto.categoria), db.joinedload(Producto.unidad))
            .filter(Producto.estado.is_(True))
        )
        if busqueda:
            patron = f"%{busqueda}%"
            consulta = consulta.filter(db.or_(Producto.nombre_producto.ilike(patron), Producto.codigo_producto.ilike(patron)))
        if solo_alerta:
            consulta = consulta.filter(db.func.coalesce(StockPorSede.cantidad_actual, 0) < Producto.stock_minimo)
        filas, total, total_paginas, pagina = paginar(
            consulta.order_by(Producto.nombre_producto), request.args.get("pagina"), PRODUCTOS_POR_PAGINA
        )
        provisionales = StockPorSede.query.filter_by(id_sede=id_sede, origen="provisional").count()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar inventario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("inventario/listar.html", **contexto_vacio), 503

    return render_template(
        "inventario/listar.html", filas=filas, sedes=sedes, id_sede=id_sede, busqueda=busqueda, solo_alerta=solo_alerta,
        pagina=pagina, total_paginas=total_paginas, total=total, provisionales=provisionales,
    )


# detalle de un producto: stock por sede, formularios de ingreso y conteo, y su kárdex
@bp.route("/<int:id_producto>")
@login_required
@rol_requerido("Administrador")
def detalle(id_producto):
    try:
        contexto = _contexto_detalle(id_producto)
    except SQLAlchemyError as error:
        registrar_error_conexion("ver inventario de un producto", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("inventario.listar"))
    # un código de envío distinto para cada formulario de la página
    return render_template("inventario/detalle.html", token_envio=uuid.uuid4().hex, token_conteo=uuid.uuid4().hex, **contexto)


def _contexto_detalle(id_producto):
    producto = db.get_or_404(Producto, id_producto)
    sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()
    stock = {s.id_sede: s for s in StockPorSede.query.filter_by(id_producto=id_producto).all()}
    movimientos = (
        MovimientoInventario.query.options(db.joinedload(MovimientoInventario.usuario), db.joinedload(MovimientoInventario.sede))
        .filter_by(id_producto=id_producto)
        .order_by(MovimientoInventario.fecha_hora.desc(), MovimientoInventario.id_movimiento.desc())
        .limit(MOVIMIENTOS_A_MOSTRAR)
        .all()
    )
    return {
        "producto": producto, "sedes": sedes, "stock": stock, "movimientos": movimientos,
        "tipos": TIPOS_MOVIMIENTO, "motivos_ingreso": MOTIVOS_INGRESO, "sede_defecto": _leer_sede(sedes, ""),
        "cantidad_maxima": CANTIDAD_MAXIMA,
    }


# lee la sede y el entero de un formulario; devuelve (sede, numero, errores)
def _leer_sede_y_numero(campo_numero, etiqueta, minimo):
    errores = []
    sede = Sede.query.filter_by(estado=True, id_sede=int(request.form["id_sede"])).first() if request.form.get("id_sede", "").isdigit() else None
    if sede is None:
        errores.append("Elige una sede válida.")
    texto = request.form.get(campo_numero, "").strip()
    numero = None
    try:
        numero = int(texto)
        if numero < minimo or numero > CANTIDAD_MAXIMA:
            raise ValueError
    except ValueError:
        errores.append(f"{etiqueta} debe ser un número entero entre {minimo} y 1 000 000.")
    return sede, numero, errores


# registra un ingreso de mercadería (compra o producción propia): suma al stock y deja el movimiento
@bp.route("/<int:id_producto>/ingreso", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def registrar_ingreso(id_producto):
    destino = url_for("inventario.detalle", id_producto=id_producto)
    try:
        producto = db.get_or_404(Producto, id_producto)
        sede, cantidad, errores = _leer_sede_y_numero("cantidad", "La cantidad", 1)
    except SQLAlchemyError as error:
        registrar_error_conexion("validar ingreso de inventario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)

    clave_motivo = request.form.get("motivo", "")
    if clave_motivo not in MOTIVOS_INGRESO:
        errores.append("Elige el motivo del ingreso.")
    detalle_texto = request.form.get("detalle", "").strip()
    validar_largo(errores, detalle_texto, 100, "El detalle")
    if errores:
        for mensaje in errores:
            flash(mensaje, "danger")
        return redirect(destino)

    motivo = MOTIVOS_INGRESO[clave_motivo] + (f": {detalle_texto}" if detalle_texto else "")
    token = (request.form.get("token_envio") or "").strip()[:40] or None
    try:
        stock_nuevo = ingresar(id_producto, sede.id_sede, cantidad, current_user.id_usuario, motivo, token_envio=token)
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if es_clave_repetida(error, "token_envio"):
            flash("Ese ingreso ya estaba registrado: se ignoró el segundo envío del formulario.", "warning")
        else:
            registrar_error_conexion("registrar ingreso de inventario", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)
    except SQLAlchemyError as error:
        registrar_error_conexion("registrar ingreso de inventario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)

    registrar_accion(
        "inventario_ingreso", "producto", id_producto,
        f"{producto.nombre_producto} en {sede.nombre_sede}: +{cantidad} (queda {stock_nuevo}); {motivo}",
    )
    flash(f'Ingreso registrado: +{cantidad} de "{producto.nombre_producto}" en {sede.nombre_sede}. Ahora hay {stock_nuevo}.', "success")
    return redirect(destino)


# corrige el stock al número contado físicamente; la diferencia queda como movimiento y el stock pasa a ser "real"
@bp.route("/<int:id_producto>/conteo", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def registrar_conteo(id_producto):
    destino = url_for("inventario.detalle", id_producto=id_producto)
    try:
        producto = db.get_or_404(Producto, id_producto)
        sede, conteo, errores = _leer_sede_y_numero("conteo", "El conteo", 0)
    except SQLAlchemyError as error:
        registrar_error_conexion("validar conteo de inventario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)

    nota = request.form.get("detalle", "").strip()
    if len(nota) < 3:
        errores.append("Escribe una breve nota del conteo (por ejemplo la fecha o quién lo hizo).")
    validar_largo(errores, nota, 100, "La nota")
    if errores:
        for mensaje in errores:
            flash(mensaje, "danger")
        return redirect(destino)

    motivo = f"Conteo físico: {nota}"
    token = (request.form.get("token_envio") or "").strip()[:40] or None
    try:
        diferencia = ajustar_a_conteo(id_producto, sede.id_sede, conteo, current_user.id_usuario, motivo, token_envio=token)
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if es_clave_repetida(error, "token_envio"):
            flash("Ese conteo ya estaba registrado: se ignoró el segundo envío del formulario.", "warning")
        else:
            registrar_error_conexion("registrar conteo de inventario", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)
    except SQLAlchemyError as error:
        registrar_error_conexion("registrar conteo de inventario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)

    registrar_accion(
        "inventario_conteo", "producto", id_producto,
        f"{producto.nombre_producto} en {sede.nombre_sede}: stock fijado en {conteo} (diferencia {diferencia:+d}); {nota}",
    )
    if diferencia == 0:
        flash(f'Conteo registrado: el stock de "{producto.nombre_producto}" ya coincidía ({conteo}).', "success")
    else:
        flash(f'Conteo registrado: el stock de "{producto.nombre_producto}" en {sede.nombre_sede} quedó en {conteo} ({diferencia:+d}).', "success")
    return redirect(destino)
