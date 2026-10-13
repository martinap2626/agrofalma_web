"""
Módulo de ventas consolidadas (Sprint 2): registrar ventas al contado o al
crédito, administrar la cartera de clientes, y llevar el seguimiento de
créditos y sus recuperaciones (cobros).

Convenciones que siguen el mismo criterio que app/productos/routes.py:
- Formularios HTML planos con validación server-side manual.
- Soft-delete (`estado`) en vez de borrado físico para clientes.
- Una venta ya registrada no se edita ni se elimina (es un comprobante).
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, es_clave_repetida, registrar_error_conexion
from app.paginacion import paginar
from app.servicios_inventario import StockInsuficiente, descontar_por_venta, devolver_por_anulacion
from app.utilidades_fecha import rango_dia, rango_fechas
from app.seguridad import leer_decimal, puede_ver_venta, registrar_accion, validar_largo
from app.validaciones import validar_documento
from app.models import (
    Categoria, Cliente, Credito, DetalleVenta, Producto, Recuperacion, Sede, StockPorSede,
    Usuario, Venta,
)

bp = Blueprint("ventas", __name__, url_prefix="/ventas")

# máximo de filas que una pantalla de listado dibuja de una vez (más que esto
# no es legible, y cada fila cuesta tiempo de servidor y de navegador)
LIMITE_FILAS = 500
# clientes por página en el listado (la búsqueda y la paginación las hace el servidor)
CLIENTES_POR_PAGINA = 25
# las ventas históricas importadas (resumen mensual) empiezan con esta letra (ver app/comandos_historico.py)
PREFIJO_HISTORICO = "H"


# ---------------------------------------------------------------------------
# Ventas — listar y registrar
# ---------------------------------------------------------------------------
# consolidado de ventas de todas las sedes, con varios filtros (fecha, trabajador, categoria, sede)
@bp.route("")
@login_required
@rol_requerido("Administrador")
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

    # sin ningún filtro de fechas, se muestran los últimos 30 días en vez de TODA la
    # historia (que crece sin parar y volvería lenta la pantalla); el filtro lo refleja
    if not desde and not hasta:
        desde = date.today() - timedelta(days=30)

    contexto_filtros = {
        "busqueda": busqueda, "desde": desde, "hasta": hasta,
        "id_trabajador": id_trabajador, "id_categoria": id_categoria, "id_sede": id_sede,
    }

    try:
        trabajadores = Usuario.query.filter_by(estado=True).order_by(Usuario.nombre).all()
        categorias = Categoria.query.filter_by(estado=True).order_by(Categoria.nombre_categoria).all()
        sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()

        # una sola consulta trae la venta con su cliente, trabajador y sede
        # (sin esto, cada fila disparaba 2 consultas más: el clásico "N+1")
        consulta = Venta.query.join(Cliente).options(
            db.contains_eager(Venta.cliente), db.joinedload(Venta.usuario), db.joinedload(Venta.sede)
        )
        if busqueda:
            consulta = consulta.filter(
                db.or_(
                    Cliente.nombre_cliente.ilike(f"%{busqueda}%"),
                    Venta.numero_operacion.ilike(f"%{busqueda}%"),
                )
            )
        if desde:
            consulta = consulta.filter(Venta.fecha_hora >= rango_fechas(desde, desde)[0])
        if hasta:
            consulta = consulta.filter(Venta.fecha_hora < rango_fechas(hasta, hasta)[1])
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
        # tope de filas en pantalla: las más recientes del filtro (el resto se ve acotando fechas)
        ventas = consulta.order_by(Venta.fecha_hora.desc()).limit(LIMITE_FILAS + 1).all()
        limite_alcanzado = len(ventas) > LIMITE_FILAS
        ventas = ventas[:LIMITE_FILAS]
    except SQLAlchemyError as error:
        registrar_error_conexion("listar ventas", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "ventas/listar.html", ventas=[], trabajadores=[], categorias=[], sedes=[],
            **contexto_filtros,
        ), 503

    return render_template(
        "ventas/listar.html", ventas=ventas, trabajadores=trabajadores, categorias=categorias,
        sedes=sedes, limite_filas=LIMITE_FILAS, limite_alcanzado=limite_alcanzado, **contexto_filtros,
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
        inicio_dia, fin_dia = rango_dia(fecha_filtro)
        consulta = Venta.query.join(Cliente).options(db.contains_eager(Venta.cliente)).filter(
            Venta.id_usuario == current_user.id_usuario,
            Venta.fecha_hora >= inicio_dia,
            Venta.fecha_hora < fin_dia,
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
            .options(db.contains_eager(Credito.venta).joinedload(Venta.cliente))
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

    # las ventas anuladas se muestran en la lista (con su etiqueta) pero no suman en los totales del día
    vigentes = [v for v in ventas if not v.anulada]
    total_contado = sum((v.total for v in vigentes if v.tipo_venta == "contado"), Decimal("0"))
    total_credito = sum((v.total for v in vigentes if v.tipo_venta == "credito"), Decimal("0"))
    cantidad_contado = sum(1 for v in vigentes if v.tipo_venta == "contado")
    cantidad_credito = sum(1 for v in vigentes if v.tipo_venta == "credito")
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
    # la lista de clientes (cientos o miles de filas) solo se carga cuando hay que
    # DIBUJAR el formulario; si la venta se guarda bien, se redirige y no hace falta
    def cargar_clientes():
        return Cliente.query.filter_by(estado=True).order_by(Cliente.nombre_cliente).all()

    def cargar_stock():
        return {
            s.id_producto: s.cantidad_actual
            for s in StockPorSede.query.filter_by(id_sede=current_user.id_sede).all()
        }

    try:
        productos = Producto.query.filter_by(estado=True).order_by(Producto.nombre_producto).all()
        stock_sede = cargar_stock()
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de nueva venta", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.mis_ventas"))

    # código único de este formulario: si llega dos veces (doble clic, reintento) solo se registra una venta.
    # Al volver a dibujar el formulario por un error se conserva el mismo código, porque la venta aún no se guardó.
    token_envio = (request.form.get("token_envio") or "").strip()[:40] or uuid.uuid4().hex

    if request.method == "POST":
        try:
            venta, rebajas = _registrar_venta(request.form, productos, token_envio)
            registrar_accion(
                "venta_registrada", "venta", venta.id_venta,
                f"{venta.numero_operacion} ({venta.tipo_venta}) total S/ {venta.total}",
            )
            for rebaja in rebajas:
                registrar_accion("venta_precio_rebajado", "venta", venta.id_venta, f"{venta.numero_operacion}: {rebaja}")
            flash(f'Venta "{venta.numero_operacion}" registrada correctamente.', "success")
            return redirect(url_for("ventas.detalle", id_venta=venta.id_venta))
        except _VentaDuplicada as duplicada:
            flash("Esa venta ya estaba registrada: se ignoró el segundo envío del formulario.", "warning")
            return redirect(url_for("ventas.detalle", id_venta=duplicada.id_venta))
        except _ErrorValidacionVenta as error:
            for mensaje in error.errores:
                flash(mensaje, "danger")
            try:
                stock_sede = cargar_stock()  # el stock pudo cambiar mientras se llenaba el formulario
            except SQLAlchemyError:
                db.session.rollback()
        except SQLAlchemyError as error:
            db.session.rollback()
            registrar_error_conexion("registrar venta", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template(
                "ventas/nueva.html", clientes=[], productos=productos, stock_sede=stock_sede, valores=request.form,
                token_envio=token_envio,
            ), 503

        return render_template(
            "ventas/nueva.html", clientes=cargar_clientes(), productos=productos, stock_sede=stock_sede,
            valores=request.form, token_envio=token_envio,
        )

    try:
        clientes = cargar_clientes()
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de nueva venta", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.mis_ventas"))
    return render_template(
        "ventas/nueva.html", clientes=clientes, productos=productos, stock_sede=stock_sede, valores=None,
        token_envio=token_envio,
    )


# muestra el detalle completo de una venta puntual
@bp.route("/<int:id_venta>")
@login_required
def detalle(id_venta):
    try:
        venta = db.get_or_404(Venta, id_venta)
    except SQLAlchemyError as error:
        registrar_error_conexion("ver detalle de venta", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.mis_ventas"))

    # un Trabajador solo puede ver el detalle de las ventas que él registró
    if not puede_ver_venta(venta):
        abort(403)

    return render_template("ventas/detalle.html", venta=venta, es_historica=venta.numero_operacion.startswith(PREFIJO_HISTORICO))


# excepcion propia para cortar el registro de la venta apenas algo no cuadra
class _ErrorValidacionVenta(Exception):
    def __init__(self, errores):
        super().__init__("Errores de validación al registrar la venta")
        self.errores = errores


# el mismo formulario llegó dos veces: ya existe una venta con ese código de envío
class _VentaDuplicada(Exception):
    def __init__(self, id_venta):
        super().__init__("Venta duplicada")
        self.id_venta = id_venta


# aqui pasa todo lo importante: valida el formulario, descuenta stock, crea el credito si aplica y guarda
def _registrar_venta(form, productos, token_envio):
    """
    Valida y registra una venta completa (cabecera + líneas + descuento de
    stock + crédito si aplica) en una sola transacción. Lanza
    _ErrorValidacionVenta si algo no es válido, sin dejar nada a medias.

    Devuelve (venta, rebajas): rebajas es la lista de textos de las líneas vendidas por debajo del precio de lista,
    para dejarlas en la bitácora (quién rebajó qué precio y cuánto).
    """
    productos_por_id = {p.id_producto: p for p in productos}
    errores = []
    # descuento máximo que un Trabajador puede dar sin autorización (el Administrador no tiene tope, pero queda registrado)
    descuento_maximo = float(current_app.config["DESCUENTO_MAX_TRABAJADOR_PCT"])
    rebajas = []

    id_cliente_raw = form.get("id_cliente", "")
    tipo_venta = form.get("tipo_venta", "contado")
    observaciones = form.get("observaciones", "").strip() or None
    validar_largo(errores, observaciones, 150, "Las observaciones")

    cliente = db.session.get(Cliente, int(id_cliente_raw)) if id_cliente_raw.isdigit() else None
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
            if cantidad <= 0 or cantidad > 1_000_000:
                raise ValueError
        except (ValueError, TypeError):
            errores.append(f'La cantidad de "{producto.nombre_producto}" debe ser un número mayor a 0.')
            continue

        try:
            precio_unitario = leer_decimal(precio_raw) if precio_raw else producto.precio_unitario
        except (InvalidOperation, ValueError):
            errores.append(f'El precio de "{producto.nombre_producto}" no es válido.')
            continue

        # control de precio: vender por debajo del de lista es posible, pero con tope para el Trabajador y con rastro
        if producto.precio_unitario > 0 and precio_unitario < producto.precio_unitario:
            descuento = float((producto.precio_unitario - precio_unitario) / producto.precio_unitario * 100)
            if not current_user.es_administrador and descuento > descuento_maximo:
                errores.append(
                    f'El precio de "{producto.nombre_producto}" está {descuento:.0f} % por debajo del precio de lista '
                    f"(S/ {producto.precio_unitario}); un Trabajador puede dar hasta {descuento_maximo:g} %. "
                    "Pide autorización al Administrador."
                )
                continue
            rebajas.append(
                f'"{producto.nombre_producto}" a S/ {precio_unitario} en lugar de S/ {producto.precio_unitario} '
                f"({descuento:.0f} % menos)"
            )

        cantidad_pedida_por_producto[producto.id_producto] = (
            cantidad_pedida_por_producto.get(producto.id_producto, 0) + cantidad
        )
        lineas.append({"producto": producto, "cantidad": cantidad, "precio_unitario": precio_unitario})

    if not lineas and not errores:
        errores.append("Agrega al menos un producto a la venta.")

    # revisión previa con el stock de AHORA (no el de cuando se abrió el formulario), para avisar de todos los productos
    # que no alcanzan de una vez; el descuento atómico de más abajo es la comprobación definitiva
    if cantidad_pedida_por_producto:
        stock_ahora = dict(
            db.session.query(StockPorSede.id_producto, StockPorSede.cantidad_actual)
            .filter(
                StockPorSede.id_sede == current_user.id_sede,
                StockPorSede.id_producto.in_(list(cantidad_pedida_por_producto)),
            )
            .all()
        )
        for id_producto, cantidad_pedida in cantidad_pedida_por_producto.items():
            disponible = stock_ahora.get(id_producto, 0)
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
        token_envio=token_envio,
        # provisional y único (así dos ventas simultáneas no chocan); el definitivo se arma abajo con el id
        numero_operacion=uuid.uuid4().hex[:15],
    )
    db.session.add(venta)
    try:
        db.session.flush()  # asigna id_venta y comprueba que el código de envío no se haya usado antes
    except IntegrityError as error:
        db.session.rollback()
        if es_clave_repetida(error, "token_envio"):
            existente = Venta.query.filter_by(token_envio=token_envio).first()
            if existente is not None:
                raise _VentaDuplicada(existente.id_venta) from error
        raise
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

    # el descuento de stock es atómico (ver servicios_inventario): si entre que se abrió el formulario y se envió otra
    # venta se llevó las unidades, aquí se detecta y no se registra nada
    try:
        descontar_por_venta(current_user.id_sede, cantidad_pedida_por_producto, current_user.id_usuario, venta.id_venta)
    except StockInsuficiente as sin_stock:
        db.session.rollback()
        nombre = productos_por_id[sin_stock.id_producto].nombre_producto
        actual = db.session.execute(
            select(StockPorSede.cantidad_actual).where(
                StockPorSede.id_producto == sin_stock.id_producto, StockPorSede.id_sede == current_user.id_sede
            )
        ).scalar() or 0
        raise _ErrorValidacionVenta(
            [f'No hay stock suficiente de "{nombre}" en tu sede (disponible: {actual}).']
        ) from sin_stock

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
    return venta, rebajas


# anula una venta mal registrada (solo Administrador): la marca, devuelve el stock y deja el motivo; nunca la borra
@bp.route("/<int:id_venta>/anular", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def anular(id_venta):
    motivo = request.form.get("motivo", "").strip()
    if len(motivo) < 5 or len(motivo) > 150:
        flash("Escribe el motivo de la anulación (entre 5 y 150 caracteres).", "danger")
        return redirect(url_for("ventas.detalle", id_venta=id_venta))

    try:
        # la venta se bloquea mientras se anula: dos anulaciones simultáneas no devolverían el stock dos veces
        venta = db.session.execute(
            select(Venta).where(Venta.id_venta == id_venta).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if venta is None:
            abort(404)
        if venta.numero_operacion.startswith(PREFIJO_HISTORICO):
            db.session.rollback()
            flash("Las ventas históricas importadas (resumen mensual) no se pueden anular.", "danger")
            return redirect(url_for("ventas.detalle", id_venta=id_venta))
        if venta.anulada:
            db.session.rollback()
            flash("Esa venta ya estaba anulada.", "warning")
            return redirect(url_for("ventas.detalle", id_venta=id_venta))
        credito = venta.credito
        if credito is not None and credito.recuperaciones:
            db.session.rollback()
            flash("Esta venta al crédito ya tiene pagos registrados, por eso no se puede anular.", "danger")
            return redirect(url_for("ventas.detalle", id_venta=id_venta))

        cantidades = {}
        for linea in venta.detalle:
            cantidades[linea.id_producto] = cantidades.get(linea.id_producto, 0) + linea.cantidad
        devolver_por_anulacion(
            venta.id_sede, cantidades, current_user.id_usuario, venta.id_venta,
            f"Anulación de {venta.numero_operacion}: {motivo}",
        )
        venta.anulada = True
        venta.fecha_anulacion = datetime.now()
        venta.id_usuario_anulacion = current_user.id_usuario
        venta.motivo_anulacion = motivo
        if credito is not None:
            credito.estado = "anulado"
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        registrar_error_conexion("anular venta", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.detalle", id_venta=id_venta))

    registrar_accion(
        "venta_anulada", "venta", venta.id_venta, f"{venta.numero_operacion} (S/ {venta.total}): {motivo}",
    )
    flash(f'Venta "{venta.numero_operacion}" anulada. El stock volvió al almacén.', "success")
    return redirect(url_for("ventas.detalle", id_venta=id_venta))


# ---------------------------------------------------------------------------
# Clientes — CRUD con soft-delete (accesible para ambos roles)
# ---------------------------------------------------------------------------
# lista de clientes con buscador (nombre o documento); busca y pagina el servidor, así sigue rápido con miles de clientes
@bp.route("/clientes")
@login_required
def listar_clientes():
    busqueda = request.args.get("q", "").strip()
    contexto_vacio = {"clientes": [], "busqueda": busqueda, "pagina": 1, "total_paginas": 1, "total": 0}

    try:
        consulta = Cliente.query
        if busqueda:
            patron = f"%{busqueda}%"
            consulta = consulta.filter(db.or_(Cliente.nombre_cliente.ilike(patron), Cliente.documento.ilike(patron)))
        clientes, total, total_paginas, pagina = paginar(
            consulta.order_by(Cliente.nombre_cliente), request.args.get("pagina"), CLIENTES_POR_PAGINA
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("listar clientes", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("clientes/listar.html", **contexto_vacio), 503

    return render_template(
        "clientes/listar.html", clientes=clientes, busqueda=busqueda, pagina=pagina, total_paginas=total_paginas, total=total,
    )


# formulario para registrar un cliente nuevo
@bp.route("/clientes/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_cliente():
    if request.method == "POST":
        try:
            datos, errores = _validar_formulario_cliente(request.form)
        except SQLAlchemyError as error:
            registrar_error_conexion("validar cliente nuevo", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template("clientes/formulario.html", cliente=None, valores=request.form), 503

        if not errores:
            cliente = Cliente(**datos)
            db.session.add(cliente)
            try:
                db.session.commit()
                registrar_accion("cliente_creado", "cliente", cliente.id_cliente, cliente.nombre_cliente)
                flash(f'Cliente "{cliente.nombre_cliente}" registrado correctamente.', "success")
                return redirect(url_for("ventas.listar_clientes"))
            except SQLAlchemyError as error:
                db.session.rollback()
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
        cliente = db.get_or_404(Cliente, id_cliente)
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de edición de cliente", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_clientes"))

    if request.method == "POST":
        try:
            datos, errores = _validar_formulario_cliente(request.form, cliente_actual=cliente)
        except SQLAlchemyError as error:
            registrar_error_conexion("validar edición de cliente", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template("clientes/formulario.html", cliente=cliente, valores=request.form), 503

        if not errores:
            for campo, valor in datos.items():
                setattr(cliente, campo, valor)
            try:
                db.session.commit()
                registrar_accion("cliente_editado", "cliente", cliente.id_cliente, cliente.nombre_cliente)
                flash(f'Cliente "{cliente.nombre_cliente}" actualizado correctamente.', "success")
                return redirect(url_for("ventas.listar_clientes"))
            except SQLAlchemyError as error:
                db.session.rollback()
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
        cliente = db.get_or_404(Cliente, id_cliente)
        cliente.estado = False
        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("desactivar cliente", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_clientes"))

    registrar_accion("cliente_desactivado", "cliente", cliente.id_cliente, cliente.nombre_cliente)
    flash(f'Cliente "{cliente.nombre_cliente}" desactivado.', "success")
    return redirect(url_for("ventas.listar_clientes"))


# reactiva un cliente que estaba desactivado
@bp.route("/clientes/<int:id_cliente>/reactivar", methods=["POST"])
@login_required
def reactivar_cliente(id_cliente):
    try:
        cliente = db.get_or_404(Cliente, id_cliente)
        cliente.estado = True
        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("reactivar cliente", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_clientes"))

    registrar_accion("cliente_reactivado", "cliente", cliente.id_cliente, cliente.nombre_cliente)
    flash(f'Cliente "{cliente.nombre_cliente}" reactivado.', "success")
    return redirect(url_for("ventas.listar_clientes"))


# valida los datos del formulario de cliente antes de crear o editar
def _validar_formulario_cliente(form, cliente_actual=None):
    errores = []

    nombre = form.get("nombre_cliente", "").strip()
    documento = form.get("documento", "").strip()
    telefono = form.get("telefono", "").strip()

    if not nombre:
        errores.append("El nombre del cliente es obligatorio.")
    validar_largo(errores, nombre, 100, "El nombre del cliente")
    validar_largo(errores, documento, 15, "El documento")
    validar_largo(errores, telefono, 15, "El teléfono")

    # el documento (DNI, RUC u otro) se valida al crear el cliente o cuando se cambia; si se edita otro dato de un cliente
    # antiguo cuyo documento ya estaba guardado, no se le exige corregirlo ahora
    documento_sin_cambios = cliente_actual is not None and (cliente_actual.documento or "") == documento
    if documento and not documento_sin_cambios:
        problema = validar_documento(documento)
        if problema:
            errores.append(problema)
        else:
            repetido = Cliente.query.filter(Cliente.documento == documento)
            if cliente_actual is not None:
                repetido = repetido.filter(Cliente.id_cliente != cliente_actual.id_cliente)
            otro = repetido.first()
            if otro is not None:
                errores.append(f'Ya existe un cliente con ese documento: "{otro.nombre_cliente}". Búscalo en la lista en vez de crearlo otra vez.')

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
        consulta = Credito.query.join(Venta).options(
            db.contains_eager(Credito.venta).joinedload(Venta.cliente)
        )
        if not current_user.es_administrador:
            consulta = consulta.filter(Venta.id_usuario == current_user.id_usuario)
        if filtro_estado in ("pendiente", "pagado"):
            consulta = consulta.filter(Credito.estado == filtro_estado)
        creditos = consulta.order_by(Credito.fecha_credito.desc()).limit(LIMITE_FILAS + 1).all()
        limite_alcanzado = len(creditos) > LIMITE_FILAS
        creditos = creditos[:LIMITE_FILAS]
    except SQLAlchemyError as error:
        registrar_error_conexion("listar créditos", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("creditos/listar.html", creditos=[], filtro_estado=filtro_estado), 503

    return render_template(
        "creditos/listar.html", creditos=creditos, filtro_estado=filtro_estado,
        limite_filas=LIMITE_FILAS, limite_alcanzado=limite_alcanzado,
    )


# muestra un credito puntual junto con los pagos que ya se le hicieron
@bp.route("/creditos/<int:id_credito>")
@login_required
def detalle_credito(id_credito):
    try:
        credito = db.get_or_404(Credito, id_credito)
    except SQLAlchemyError as error:
        registrar_error_conexion("ver detalle de crédito", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_creditos"))

    if not puede_ver_venta(credito.venta):
        abort(403)

    return render_template("creditos/detalle.html", credito=credito, token_envio=uuid.uuid4().hex)


# registra un pago (recuperacion) contra un credito y va bajando el saldo pendiente
@bp.route("/creditos/<int:id_credito>/pagar", methods=["POST"])
@login_required
def registrar_pago(id_credito):
    try:
        credito = db.get_or_404(Credito, id_credito)
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir crédito para registrar pago", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("ventas.listar_creditos"))

    if not puede_ver_venta(credito.venta):
        abort(403)

    destino = url_for("ventas.detalle_credito", id_credito=id_credito)
    token_envio = (request.form.get("token_envio") or "").strip()[:40] or None

    try:
        monto = leer_decimal(request.form.get("monto_recuperado", ""))
    except (InvalidOperation, ValueError):
        flash("El monto ingresado no es válido.", "danger")
        return redirect(destino)

    try:
        # el crédito se bloquea mientras se registra el pago: dos pagos simultáneos se ponen en fila y cada uno ve el saldo
        # ya descontado por el anterior (antes ambos leían el mismo saldo y el segundo pisaba al primero)
        credito = db.session.execute(
            select(Credito).where(Credito.id_credito == id_credito).with_for_update().execution_options(populate_existing=True)
        ).scalar_one()

        if credito.estado == "anulado":
            db.session.rollback()
            flash("Este crédito pertenece a una venta anulada.", "danger")
            return redirect(destino)
        if credito.estado == "pagado":
            db.session.rollback()
            flash("Este crédito ya está saldado.", "danger")
            return redirect(destino)
        if monto <= 0 or monto > credito.saldo_pendiente:
            db.session.rollback()
            flash(f"El monto debe ser mayor a 0 y no puede superar el saldo pendiente (S/ {credito.saldo_pendiente}).", "danger")
            return redirect(destino)

        credito.saldo_pendiente -= monto
        db.session.add(
            Recuperacion(
                id_credito=credito.id_credito,
                monto_recuperado=monto,
                fecha_recuperacion=date.today(),
                id_usuario=current_user.id_usuario,
                token_envio=token_envio,
            )
        )
        if credito.saldo_pendiente <= 0:
            credito.estado = "pagado"
            credito.venta.estado_pago = "pagado"

        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if es_clave_repetida(error, "token_envio"):
            flash("Ese pago ya estaba registrado: se ignoró el segundo envío del formulario.", "warning")
            return redirect(destino)
        registrar_error_conexion("registrar pago de crédito", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)
    except SQLAlchemyError as error:
        db.session.rollback()
        registrar_error_conexion("registrar pago de crédito", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(destino)

    registrar_accion(
        "pago_registrado", "credito", credito.id_credito,
        f"S/ {monto} al crédito de {credito.venta.numero_operacion}; saldo S/ {credito.saldo_pendiente}",
    )
    flash("Pago registrado correctamente.", "success")
    return redirect(destino)
