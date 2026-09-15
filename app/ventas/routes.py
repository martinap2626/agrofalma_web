"""
Módulo de ventas consolidadas (Sprint 2): registrar ventas al contado o al
crédito, administrar la cartera de clientes, y llevar el seguimiento de
créditos y sus recuperaciones (cobros).

Convenciones que siguen el mismo criterio que app/productos/routes.py:
- Formularios HTML planos con validación server-side manual.
- Soft-delete (`estado`) en vez de borrado físico para clientes.
- Una venta ya registrada no se edita ni se elimina (es un comprobante).
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.models import (
    Categoria, Cliente, Credito, DetalleVenta, Producto, Recuperacion, Sede, StockPorSede,
    Usuario, Venta,
)

bp = Blueprint("ventas", __name__, url_prefix="/ventas")


# ---------------------------------------------------------------------------
# Ventas — listar y registrar
# ---------------------------------------------------------------------------
# consolidado de ventas de todas las sedes, con varios filtros (fecha, trabajador, categoria, sede)
@bp.route("")
@login_required
def listar():
    """
    HU-05 (RF-05): consolidado de ventas de todas las sedes, con filtro por
    fecha, trabajador, categoría y sede (además del buscador dinámico en
    cliente ya existente por cliente/N° de operación, que sigue operando
    del lado del navegador sobre las filas que trae esta consulta).
    """
    busqueda = request.args.get("q", "").strip()

    try:
        desde = date.fromisoformat(request.args.get("desde", "").strip())
    except ValueError:
        desde = None
    try:
        hasta = date.fromisoformat(request.args.get("hasta", "").strip())
    except ValueError:
        hasta = None

    id_trabajador_raw = request.args.get("id_trabajador", "").strip()
    id_categoria_raw = request.args.get("id_categoria", "").strip()
    id_sede_raw = request.args.get("id_sede", "").strip()
    id_trabajador = int(id_trabajador_raw) if id_trabajador_raw.isdigit() else None
    id_categoria = int(id_categoria_raw) if id_categoria_raw.isdigit() else None
    id_sede = int(id_sede_raw) if id_sede_raw.isdigit() else None

    contexto_filtros = {
        "busqueda": busqueda, "desde": desde, "hasta": hasta,
        "id_trabajador": id_trabajador, "id_categoria": id_categoria, "id_sede": id_sede,
    }

    try:
        trabajadores = Usuario.query.filter_by(estado=True).order_by(Usuario.nombre).all()
        categorias = Categoria.query.filter_by(estado=True).order_by(Categoria.nombre_categoria).all()
        sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()

        consulta = Venta.query.join(Cliente)
        if busqueda:
            consulta = consulta.filter(
                db.or_(
                    Cliente.nombre_cliente.ilike(f"%{busqueda}%"),
                    Venta.numero_operacion.ilike(f"%{busqueda}%"),
                )
            )
        if desde:
            consulta = consulta.filter(db.func.date(Venta.fecha_hora) >= desde)
        if hasta:
            consulta = consulta.filter(db.func.date(Venta.fecha_hora) <= hasta)
        if id_trabajador:
            consulta = consulta.filter(Venta.id_usuario == id_trabajador)
        if id_sede:
            consulta = consulta.filter(Venta.id_sede == id_sede)
        if id_categoria:
            consulta = consulta.filter(
                Venta.id_venta.in_(
                    db.session.query(DetalleVenta.id_venta)
                    .join(Producto, Producto.id_producto == DetalleVenta.id_producto)
                    .filter(Producto.id_categoria == id_categoria)
                )
            )
        ventas = consulta.order_by(Venta.fecha_hora.desc()).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar ventas", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "ventas/listar.html", ventas=[], trabajadores=[], categorias=[], sedes=[],
            **contexto_filtros,
        ), 503

    return render_template(
        "ventas/listar.html", ventas=ventas, trabajadores=trabajadores, categorias=categorias,
        sedes=sedes, **contexto_filtros,
    )


# lo que vendio hoy (o el dia que elija) el usuario que esta logueado, mas su cartera de creditos pendientes
@bp.route("/mis-ventas")
@login_required
def mis_ventas():
    """
    Ventas del día registradas por el usuario actual (vendedor). A
    diferencia de listar() -que muestra el consolidado de todas las
    sedes para HU-05-, esta vista está acotada a lo que ese trabajador
    vendió, con foco operativo diario en vez de gerencial.
    """
    busqueda = request.args.get("q", "").strip()
    fecha_raw = request.args.get("fecha", "").strip()

    try:
        fecha_filtro = date.fromisoformat(fecha_raw) if fecha_raw else date.today()
    except ValueError:
        fecha_filtro = date.today()

    contexto_vacio = {
        "ventas": [], "fecha_filtro": fecha_filtro, "busqueda": busqueda,
        "total_contado": Decimal("0"), "total_credito": Decimal("0"), "recuperaciones_hoy": Decimal("0"),
        "cantidad_contado": 0, "cantidad_credito": 0,
        "creditos_pendientes": [], "saldo_pendiente_total": Decimal("0"), "stock_bajo": [],
    }

    try:
        consulta = Venta.query.join(Cliente).filter(
            Venta.id_usuario == current_user.id_usuario,
            db.func.date(Venta.fecha_hora) == fecha_filtro,
        )
        if busqueda:
            consulta = consulta.filter(
                db.or_(
                    Cliente.nombre_cliente.ilike(f"%{busqueda}%"),
                    Venta.numero_operacion.ilike(f"%{busqueda}%"),
                )
            )
        ventas = consulta.order_by(Venta.fecha_hora.desc()).all()

        recuperaciones_hoy = db.session.query(
            db.func.coalesce(db.func.sum(Recuperacion.monto_recuperado), 0)
        ).filter(
            Recuperacion.id_usuario == current_user.id_usuario,
            Recuperacion.fecha_recuperacion == fecha_filtro,
        ).scalar()

        # Créditos pendientes de cobrar: es un recordatorio de cartera, no
        # depende de la fecha filtrada arriba (una deuda no deja de existir
        # porque hoy estés mirando otro día en el listado de ventas).
        creditos_pendientes = (
            Credito.query.join(Venta)
            .filter(Venta.id_usuario == current_user.id_usuario, Credito.estado == "pendiente")
            .order_by(Credito.fecha_credito.asc())
            .all()
        )

        # Stock bajo mínimo de su propia sede: mismo criterio que el
        # dashboard del Administrador (app/dashboard/routes.py), pero sin
        # selector de sede porque el trabajador solo tiene la suya.
        filas_stock_bajo = (
            db.session.query(Producto.nombre_producto, StockPorSede.cantidad_actual, Producto.stock_minimo)
            .join(StockPorSede, StockPorSede.id_producto == Producto.id_producto)
            .filter(
                StockPorSede.id_sede == current_user.id_sede,
                StockPorSede.cantidad_actual < Producto.stock_minimo,
                Producto.estado.is_(True),
            )
            .order_by(StockPorSede.cantidad_actual.asc())
            .all()
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("listar mis ventas", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("ventas/mis_ventas.html", **contexto_vacio), 503

    total_contado = sum((v.total for v in ventas if v.tipo_venta == "contado"), Decimal("0"))
    total_credito = sum((v.total for v in ventas if v.tipo_venta == "credito"), Decimal("0"))
    cantidad_contado = sum(1 for v in ventas if v.tipo_venta == "contado")
    cantidad_credito = sum(1 for v in ventas if v.tipo_venta == "credito")
    saldo_pendiente_total = sum((c.saldo_pendiente for c in creditos_pendientes), Decimal("0"))

    return render_template(
        "ventas/mis_ventas.html",
        ventas=ventas, fecha_filtro=fecha_filtro, busqueda=busqueda,
        total_contado=total_contado, total_credito=total_credito, recuperaciones_hoy=recuperaciones_hoy,
        cantidad_contado=cantidad_contado, cantidad_credito=cantidad_credito,
        creditos_pendientes=creditos_pendientes, saldo_pendiente_total=saldo_pendiente_total,
        stock_bajo=[
            {"producto": nombre, "actual": actual, "minimo": minimo}
            for nombre, actual, minimo in filas_stock_bajo
        ],
    )


# formulario para registrar una venta nueva
@bp.route("/nueva", methods=["GET", "POST"])
@login_required
def nueva():
    try:
        clientes = Cliente.query.filter_by(estado=True).order_by(Cliente.nombre_cliente).all()
        productos = Producto.query.filter_by(estado=True).order_by(Producto.nombre_producto).all()
        stock_sede = {
            s.id_producto: s.cantidad_actual
            for s in StockPorSede.query.filter_by(id_sede=current_user.id_sede).all()
        }
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de nueva venta", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar"))

    if request.method == "POST":
        try:
            venta = _registrar_venta(request.form, productos, stock_sede)
            flash(f'Venta "{venta.numero_operacion}" registrada correctamente.', "success")
            return redirect(url_for("ventas.detalle", id_venta=venta.id_venta))
        except _ErrorValidacionVenta as error:
            for mensaje in error.errores:
                flash(mensaje, "danger")
        except SQLAlchemyError as error:
            registrar_error_conexion("registrar venta", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template(
                "ventas/nueva.html", clientes=clientes, productos=productos, stock_sede=stock_sede, valores=request.form
            ), 503

        return render_template(
            "ventas/nueva.html", clientes=clientes, productos=productos, stock_sede=stock_sede, valores=request.form
        )

    return render_template("ventas/nueva.html", clientes=clientes, productos=productos, stock_sede=stock_sede, valores=None)


# muestra el detalle completo de una venta puntual
@bp.route("/<int:id_venta>")
@login_required
def detalle(id_venta):
    try:
        venta = Venta.query.get_or_404(id_venta)
    except SQLAlchemyError as error:
        registrar_error_conexion("ver detalle de venta", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar"))

    return render_template("ventas/detalle.html", venta=venta)


# excepcion propia para cortar el registro de la venta apenas algo no cuadra
class _ErrorValidacionVenta(Exception):
    def __init__(self, errores):
        super().__init__("Errores de validación al registrar la venta")
        self.errores = errores


# aqui pasa todo lo importante: valida el formulario, descuenta stock, crea el credito si aplica y guarda
def _registrar_venta(form, productos, stock_sede):
    """
    Valida y registra una venta completa (cabecera + líneas + descuento de
    stock + crédito si aplica) en una sola transacción. Lanza
    _ErrorValidacionVenta si algo no es válido, sin tocar la base de datos.
    """
    productos_por_id = {p.id_producto: p for p in productos}
    errores = []

    id_cliente_raw = form.get("id_cliente", "")
    tipo_venta = form.get("tipo_venta", "contado")
    observaciones = form.get("observaciones", "").strip() or None

    cliente = Cliente.query.get(id_cliente_raw) if id_cliente_raw.isdigit() else None
    if cliente is None or not cliente.estado:
        errores.append("Selecciona un cliente válido.")

    if tipo_venta not in ("contado", "credito"):
        errores.append("El tipo de venta no es válido.")

    ids_producto = form.getlist("id_producto[]")
    cantidades = form.getlist("cantidad[]")
    precios = form.getlist("precio_unitario[]")

    # Acumula cantidades por producto (por si la misma línea se repite) para
    # validar el stock disponible contra el total pedido de ese producto.
    cantidad_pedida_por_producto = {}
    lineas = []

    for id_producto_raw, cantidad_raw, precio_raw in zip(ids_producto, cantidades, precios):
        if not id_producto_raw and not cantidad_raw:
            continue  # fila vacía (el usuario agregó una línea y no la usó)

        producto = productos_por_id.get(int(id_producto_raw)) if id_producto_raw.isdigit() else None
        if producto is None:
            errores.append("Uno de los productos seleccionados no es válido.")
            continue

        try:
            cantidad = int(cantidad_raw)
            if cantidad <= 0:
                raise ValueError
        except (ValueError, TypeError):
            errores.append(f'La cantidad de "{producto.nombre_producto}" debe ser un número mayor a 0.')
            continue

        try:
            precio_unitario = Decimal(precio_raw) if precio_raw else producto.precio_unitario
            if precio_unitario < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            errores.append(f'El precio de "{producto.nombre_producto}" no es válido.')
            continue

        cantidad_pedida_por_producto[producto.id_producto] = (
            cantidad_pedida_por_producto.get(producto.id_producto, 0) + cantidad
        )
        lineas.append({"producto": producto, "cantidad": cantidad, "precio_unitario": precio_unitario})

    if not lineas:
        errores.append("Agrega al menos un producto a la venta.")

    for id_producto, cantidad_pedida in cantidad_pedida_por_producto.items():
        disponible = stock_sede.get(id_producto, 0)
        if cantidad_pedida > disponible:
            nombre = productos_por_id[id_producto].nombre_producto
            errores.append(f'No hay stock suficiente de "{nombre}" en tu sede (disponible: {disponible}).')

    if errores:
        raise _ErrorValidacionVenta(errores)

    total = sum((linea["precio_unitario"] * linea["cantidad"] for linea in lineas), Decimal("0"))

    venta = Venta(
        id_cliente=cliente.id_cliente,
        id_usuario=current_user.id_usuario,
        id_sede=current_user.id_sede,
        tipo_venta=tipo_venta,
        estado_pago="pendiente" if tipo_venta == "credito" else "pagado",
        total=total,
        observaciones=observaciones,
        numero_operacion="",  # se completa abajo, una vez que exista id_venta
    )
    db.session.add(venta)
    db.session.flush()  # asigna id_venta sin cerrar la transacción
    venta.numero_operacion = f"OP-{venta.id_venta:04d}"

    for linea in lineas:
        subtotal = linea["precio_unitario"] * linea["cantidad"]
        db.session.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=linea["producto"].id_producto,
                cantidad=linea["cantidad"],
                precio_unitario=linea["precio_unitario"],
                subtotal=subtotal,
            )
        )

    for id_producto, cantidad_pedida in cantidad_pedida_por_producto.items():
        stock = StockPorSede.query.filter_by(id_producto=id_producto, id_sede=current_user.id_sede).first()
        stock.cantidad_actual -= cantidad_pedida

    if tipo_venta == "credito":
        db.session.add(
            Credito(
                id_venta=venta.id_venta,
                monto_credito=total,
                fecha_credito=date.today(),
                saldo_pendiente=total,
                estado="pendiente",
            )
        )

    db.session.commit()
    return venta


# ---------------------------------------------------------------------------
# Clientes — CRUD con soft-delete (accesible para ambos roles)
# ---------------------------------------------------------------------------
# lista de clientes, con buscador por nombre
@bp.route("/clientes")
@login_required
def listar_clientes():
    busqueda = request.args.get("q", "").strip()

    try:
        consulta = Cliente.query
        if busqueda:
            consulta = consulta.filter(Cliente.nombre_cliente.ilike(f"%{busqueda}%"))
        clientes = consulta.order_by(Cliente.nombre_cliente).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar clientes", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("clientes/listar.html", clientes=[], busqueda=busqueda), 503

    return render_template("clientes/listar.html", clientes=clientes, busqueda=busqueda)


# formulario para registrar un cliente nuevo
@bp.route("/clientes/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_cliente():
    if request.method == "POST":
        datos, errores = _validar_formulario_cliente(request.form)

        if not errores:
            cliente = Cliente(**datos)
            db.session.add(cliente)
            try:
                db.session.commit()
                flash(f'Cliente "{cliente.nombre_cliente}" registrado correctamente.', "success")
                return redirect(url_for("ventas.listar_clientes"))
            except SQLAlchemyError as error:
                registrar_error_conexion("registrar cliente", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template("clientes/formulario.html", cliente=None, valores=request.form), 503

        for error in errores:
            flash(error, "danger")
        return render_template("clientes/formulario.html", cliente=None, valores=request.form)

    return render_template("clientes/formulario.html", cliente=None, valores={})


# formulario para editar un cliente que ya existe
@bp.route("/clientes/<int:id_cliente>/editar", methods=["GET", "POST"])
@login_required
def editar_cliente(id_cliente):
    try:
        cliente = Cliente.query.get_or_404(id_cliente)
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de edición de cliente", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_clientes"))

    if request.method == "POST":
        datos, errores = _validar_formulario_cliente(request.form)

        if not errores:
            for campo, valor in datos.items():
                setattr(cliente, campo, valor)
            try:
                db.session.commit()
                flash(f'Cliente "{cliente.nombre_cliente}" actualizado correctamente.', "success")
                return redirect(url_for("ventas.listar_clientes"))
            except SQLAlchemyError as error:
                registrar_error_conexion("actualizar cliente", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template("clientes/formulario.html", cliente=cliente, valores=request.form), 503

        for error in errores:
            flash(error, "danger")
        return render_template("clientes/formulario.html", cliente=cliente, valores=request.form)

    return render_template("clientes/formulario.html", cliente=cliente, valores=None)


# desactiva un cliente (no se borra de la bd)
@bp.route("/clientes/<int:id_cliente>/desactivar", methods=["POST"])
@login_required
def desactivar_cliente(id_cliente):
    try:
        cliente = Cliente.query.get_or_404(id_cliente)
        cliente.estado = False
        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("desactivar cliente", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_clientes"))

    flash(f'Cliente "{cliente.nombre_cliente}" desactivado.', "success")
    return redirect(url_for("ventas.listar_clientes"))


# reactiva un cliente que estaba desactivado
@bp.route("/clientes/<int:id_cliente>/reactivar", methods=["POST"])
@login_required
def reactivar_cliente(id_cliente):
    try:
        cliente = Cliente.query.get_or_404(id_cliente)
        cliente.estado = True
        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("reactivar cliente", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_clientes"))

    flash(f'Cliente "{cliente.nombre_cliente}" reactivado.', "success")
    return redirect(url_for("ventas.listar_clientes"))


# valida los datos del formulario de cliente antes de crear o editar
def _validar_formulario_cliente(form):
    errores = []

    nombre = form.get("nombre_cliente", "").strip()
    documento = form.get("documento", "").strip()
    telefono = form.get("telefono", "").strip()

    if not nombre:
        errores.append("El nombre del cliente es obligatorio.")

    datos = {
        "nombre_cliente": nombre,
        "documento": documento or None,
        "telefono": telefono or None,
    }
    return datos, errores


# ---------------------------------------------------------------------------
# Créditos y cobros
# ---------------------------------------------------------------------------
# lista los creditos, filtrable por pendiente o pagado
@bp.route("/creditos")
@login_required
def listar_creditos():
    filtro_estado = request.args.get("estado", "").strip()

    try:
        consulta = Credito.query.join(Venta)
        if filtro_estado in ("pendiente", "pagado"):
            consulta = consulta.filter(Credito.estado == filtro_estado)
        creditos = consulta.order_by(Credito.fecha_credito.desc()).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar créditos", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("creditos/listar.html", creditos=[], filtro_estado=filtro_estado), 503

    return render_template("creditos/listar.html", creditos=creditos, filtro_estado=filtro_estado)


# muestra un credito puntual junto con los pagos que ya se le hicieron
@bp.route("/creditos/<int:id_credito>")
@login_required
def detalle_credito(id_credito):
    try:
        credito = Credito.query.get_or_404(id_credito)
    except SQLAlchemyError as error:
        registrar_error_conexion("ver detalle de crédito", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_creditos"))

    return render_template("creditos/detalle.html", credito=credito)


# registra un pago (recuperacion) contra un credito y va bajando el saldo pendiente
@bp.route("/creditos/<int:id_credito>/pagar", methods=["POST"])
@login_required
def registrar_pago(id_credito):
    try:
        credito = Credito.query.get_or_404(id_credito)
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir crédito para registrar pago", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_creditos"))

    monto_raw = request.form.get("monto_recuperado", "")

    try:
        monto = Decimal(monto_raw)
    except (InvalidOperation, ValueError):
        flash("El monto ingresado no es válido.", "danger")
        return redirect(url_for("ventas.detalle_credito", id_credito=id_credito))

    if credito.estado == "pagado":
        flash("Este crédito ya está saldado.", "danger")
        return redirect(url_for("ventas.detalle_credito", id_credito=id_credito))

    if monto <= 0 or monto > credito.saldo_pendiente:
        flash(f"El monto debe ser mayor a 0 y no puede superar el saldo pendiente (S/ {credito.saldo_pendiente}).", "danger")
        return redirect(url_for("ventas.detalle_credito", id_credito=id_credito))

    try:
        credito.saldo_pendiente -= monto
        db.session.add(
            Recuperacion(
                id_credito=credito.id_credito,
                monto_recuperado=monto,
                fecha_recuperacion=date.today(),
                id_usuario=current_user.id_usuario,
            )
        )

        if credito.saldo_pendiente <= 0:
            credito.estado = "pagado"
            credito.venta.estado_pago = "pagado"

        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("registrar pago de crédito", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.detalle_credito", id_credito=id_credito)), 503

    flash("Pago registrado correctamente.", "success")
    return redirect(url_for("ventas.detalle_credito", id_credito=id_credito))
