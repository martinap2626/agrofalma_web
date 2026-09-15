"""
Módulo de productos.

HU-02 (RF-03, solo Administrador): registrar, editar y desactivar
productos del catálogo.
HU-03 (RF-07, Administrador y Trabajador): consultar el catálogo con
buscador por nombre.

Ambas historias comparten la misma vista de listado: el trabajador ve
la tabla y el buscador; el administrador ve, además, las acciones de
"Nuevo producto", "Editar" y "Desactivar". La diferencia se resuelve en
la plantilla con `current_user.es_administrador`, y las rutas que
modifican datos están protegidas también en el servidor con
`@rol_requerido`, nunca solo ocultando el botón en el HTML.
"""

from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion as _registrar_error_conexion
from app.models import Producto, Categoria, UnidadMedida

bp = Blueprint("productos", __name__, url_prefix="/productos")


# ---------------------------------------------------------------------------
# HU-03 — Consultar el catálogo de productos (Administrador y Trabajador)
# ---------------------------------------------------------------------------
# muestra el catalogo completo, con buscador opcional por nombre
@bp.route("")
@login_required
def listar():
    busqueda = request.args.get("q", "").strip()

    try:
        consulta = Producto.query
        if busqueda:
            consulta = consulta.filter(Producto.nombre_producto.ilike(f"%{busqueda}%"))
        productos = consulta.order_by(Producto.nombre_producto).all()
    except SQLAlchemyError as error:
        _registrar_error_conexion("listar productos", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("productos/listar.html", productos=[], busqueda=busqueda), 503

    return render_template("productos/listar.html", productos=productos, busqueda=busqueda)


# ---------------------------------------------------------------------------
# HU-02 — Registrar un nuevo producto (solo Administrador)
# ---------------------------------------------------------------------------
# formulario para crear un producto nuevo (GET lo muestra, POST lo guarda)
@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def nuevo():
    try:
        categorias = Categoria.query.filter_by(estado=True).order_by(Categoria.nombre_categoria).all()
        unidades = UnidadMedida.query.order_by(UnidadMedida.nombre_unidad).all()
    except SQLAlchemyError as error:
        _registrar_error_conexion("abrir formulario de nuevo producto", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("productos.listar"))

    if request.method == "POST":
        try:
            datos, errores = _validar_formulario(request.form)
        except SQLAlchemyError as error:
            _registrar_error_conexion("validar nuevo producto", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template(
                "productos/formulario.html",
                categorias=categorias,
                unidades=unidades,
                producto=None,
                valores=request.form,
            ), 503

        if not errores:
            producto = Producto(**datos)
            db.session.add(producto)
            try:
                db.session.commit()
                flash(f'Producto "{producto.nombre_producto}" registrado correctamente.', "success")
                return redirect(url_for("productos.listar"))
            except IntegrityError:
                db.session.rollback()
                errores.append("El código de producto ya existe. Usa un código distinto.")
            except SQLAlchemyError as error:
                _registrar_error_conexion("registrar producto", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template(
                    "productos/formulario.html",
                    categorias=categorias,
                    unidades=unidades,
                    producto=None,
                    valores=request.form,
                ), 503

        for error in errores:
            flash(error, "danger")

        return render_template(
            "productos/formulario.html",
            categorias=categorias,
            unidades=unidades,
            producto=None,
            valores=request.form,
        )

    return render_template(
        "productos/formulario.html", categorias=categorias, unidades=unidades, producto=None, valores={}
    )


# ---------------------------------------------------------------------------
# HU-02 — Editar un producto existente (solo Administrador)
# ---------------------------------------------------------------------------
# formulario para editar un producto que ya existe
@bp.route("/<int:id_producto>/editar", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def editar(id_producto):
    try:
        producto = Producto.query.get_or_404(id_producto)
        categorias = Categoria.query.filter_by(estado=True).order_by(Categoria.nombre_categoria).all()
        unidades = UnidadMedida.query.order_by(UnidadMedida.nombre_unidad).all()
    except SQLAlchemyError as error:
        _registrar_error_conexion("abrir formulario de edición de producto", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("productos.listar"))

    if request.method == "POST":
        try:
            datos, errores = _validar_formulario(request.form, id_producto_actual=id_producto)
        except SQLAlchemyError as error:
            _registrar_error_conexion("validar edición de producto", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template(
                "productos/formulario.html",
                categorias=categorias,
                unidades=unidades,
                producto=producto,
                valores=request.form,
            ), 503

        if not errores:
            for campo, valor in datos.items():
                setattr(producto, campo, valor)
            try:
                db.session.commit()
                flash(f'Producto "{producto.nombre_producto}" actualizado correctamente.', "success")
                return redirect(url_for("productos.listar"))
            except IntegrityError:
                db.session.rollback()
                errores.append("El código de producto ya existe. Usa un código distinto.")
            except SQLAlchemyError as error:
                _registrar_error_conexion("actualizar producto", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template(
                    "productos/formulario.html",
                    categorias=categorias,
                    unidades=unidades,
                    producto=producto,
                    valores=request.form,
                ), 503

        for error in errores:
            flash(error, "danger")

        return render_template(
            "productos/formulario.html",
            categorias=categorias,
            unidades=unidades,
            producto=producto,
            valores=request.form,
        )

    return render_template(
        "productos/formulario.html", categorias=categorias, unidades=unidades, producto=producto, valores=None
    )


# ---------------------------------------------------------------------------
# HU-02 — Desactivar un producto (solo Administrador)
# ---------------------------------------------------------------------------
# apaga el producto (soft delete), no lo borra de la bd
@bp.route("/<int:id_producto>/desactivar", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def desactivar(id_producto):
    try:
        producto = Producto.query.get_or_404(id_producto)
        producto.estado = False
        db.session.commit()
    except SQLAlchemyError as error:
        _registrar_error_conexion("desactivar producto", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("productos.listar"))

    flash(f'Producto "{producto.nombre_producto}" desactivado.', "success")
    return redirect(url_for("productos.listar"))


# ---------------------------------------------------------------------------
# HU-02 — Reactivar un producto previamente desactivado (solo Administrador)
# ---------------------------------------------------------------------------
# vuelve a activar un producto que estaba desactivado
@bp.route("/<int:id_producto>/reactivar", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def reactivar(id_producto):
    try:
        producto = Producto.query.get_or_404(id_producto)
        producto.estado = True
        db.session.commit()
    except SQLAlchemyError as error:
        _registrar_error_conexion("reactivar producto", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("productos.listar"))

    flash(f'Producto "{producto.nombre_producto}" reactivado.', "success")
    return redirect(url_for("productos.listar"))


# ---------------------------------------------------------------------------
# Validación server-side (criterio de aceptación de HU-02):
# código único, precio y stock mínimo no negativos, campos obligatorios.
# ---------------------------------------------------------------------------
# revisa que los datos del formulario esten completos y correctos antes de guardar
def _validar_formulario(form, id_producto_actual=None):
    errores = []

    codigo = form.get("codigo_producto", "").strip()
    nombre = form.get("nombre_producto", "").strip()
    id_categoria = form.get("id_categoria", "")
    id_unidad = form.get("id_unidad", "")
    precio_raw = form.get("precio_unitario", "")
    stock_min_raw = form.get("stock_minimo", "0")
    descripcion = form.get("descripcion", "").strip()

    if not codigo:
        errores.append("El código de producto es obligatorio.")
    else:
        existente = Producto.query.filter_by(codigo_producto=codigo).first()
        if existente and existente.id_producto != id_producto_actual:
            errores.append("El código de producto ya está en uso.")

    if not nombre:
        errores.append("El nombre del producto es obligatorio.")

    try:
        precio = Decimal(precio_raw)
        if precio < 0:
            errores.append("El precio no puede ser negativo.")
    except (InvalidOperation, ValueError):
        errores.append("El precio ingresado no es válido.")
        precio = Decimal("0")

    try:
        stock_minimo = int(stock_min_raw or 0)
        if stock_minimo < 0:
            errores.append("El stock mínimo no puede ser negativo.")
    except ValueError:
        errores.append("El stock mínimo ingresado no es válido.")
        stock_minimo = 0

    if not id_categoria:
        errores.append("Debes seleccionar una categoría.")
    if not id_unidad:
        errores.append("Debes seleccionar una unidad de medida.")

    datos = {
        "codigo_producto": codigo,
        "nombre_producto": nombre,
        "id_categoria": int(id_categoria) if id_categoria else None,
        "id_unidad": int(id_unidad) if id_unidad else None,
        "precio_unitario": precio,
        "stock_minimo": stock_minimo,
        "descripcion": descripcion or None,
    }

    return datos, errores
