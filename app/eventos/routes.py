"""
Eventos del negocio (solo Administrador).

El gerente anota aquí lo que explica las ventas de un mes, una campaña o un año: un quiebre de stock, una plaga,
una promoción, un problema de producción. Es información que los Excel no guardan y que mejora el análisis:

  - Un quiebre de stock con cantidad no atendida se SUMA a la demanda de ese mes en la analítica (la venta perdida
    también era demanda), así las recomendaciones no subestiman productos que se agotaron.
  - Los demás eventos se muestran junto a los resultados para interpretar meses atípicos.
"""

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
from app.models import EventoNegocio, Producto, Sede
from app.models.evento_negocio import IMPACTOS, TIPOS_EVENTO
from app.seguridad import describir_cambios, registrar_accion, validar_largo

bp = Blueprint("eventos", __name__, url_prefix="/eventos")

CANTIDAD_MAXIMA = 1_000_000


def _fecha(texto):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


# lee y valida el formulario; devuelve (datos, errores)
def _validar(form):
    errores = []
    tipo = form.get("tipo", "")
    if tipo not in TIPOS_EVENTO:
        errores.append("Elige el tipo de evento.")
    inicio, fin = _fecha(form.get("fecha_inicio")), _fecha(form.get("fecha_fin"))
    if inicio is None or not (2000 <= inicio.year <= 2100):
        errores.append("Indica la fecha de inicio del evento.")
    if form.get("fecha_fin", "").strip() and fin is None:
        errores.append("La fecha de fin no es válida.")
    if inicio and fin and fin < inicio:
        errores.append("La fecha de fin no puede ser anterior a la de inicio.")
    descripcion = form.get("descripcion", "").strip()
    if not descripcion:
        errores.append("Describe brevemente qué pasó.")
    validar_largo(errores, descripcion, 200, "La descripción")
    impacto = form.get("impacto", "medio")
    if impacto not in IMPACTOS:
        errores.append("Elige el impacto estimado.")

    id_producto = form.get("id_producto", "").strip()
    producto = None
    if id_producto:
        producto = db.session.get(Producto, int(id_producto)) if id_producto.isdigit() else None
        if producto is None:
            errores.append("El producto elegido no existe.")
    id_sede = form.get("id_sede", "").strip()
    sede = None
    if id_sede:
        sede = db.session.get(Sede, int(id_sede)) if id_sede.isdigit() else None
        if sede is None:
            errores.append("La sede elegida no existe.")

    cantidad = None
    texto_cantidad = form.get("cantidad_no_atendida", "").strip()
    if texto_cantidad:
        if tipo != "quiebre_stock":
            errores.append("La cantidad no atendida solo se anota en un quiebre de stock.")
        elif not texto_cantidad.isdigit() or not (0 < int(texto_cantidad) <= CANTIDAD_MAXIMA):
            errores.append("La cantidad no atendida debe ser un entero positivo.")
        elif producto is None:
            errores.append("Para anotar la cantidad no atendida elige el producto que se agotó.")
        else:
            cantidad = int(texto_cantidad)

    datos = {
        "es_ejemplo": form.get("es_ejemplo") == "on",
        "tipo": tipo, "fecha_inicio": inicio, "fecha_fin": fin, "descripcion": descripcion, "impacto": impacto,
        "id_producto": producto.id_producto if producto else None, "id_sede": sede.id_sede if sede else None,
        "cantidad_no_atendida": cantidad,
    }
    return datos, errores


def _listas():
    return (Producto.query.filter_by(estado=True).order_by(Producto.nombre_producto).all(),
            Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all())


@bp.route("")
@login_required
@rol_requerido("Administrador")
def listar():
    anio_txt = request.args.get("anio", "").strip()
    campana = request.args.get("campana", "").strip()
    tipo = request.args.get("tipo", "").strip()
    anio = int(anio_txt) if anio_txt.isdigit() and 2000 <= int(anio_txt) <= 2100 else None
    try:
        consulta = EventoNegocio.query.options(db.joinedload(EventoNegocio.producto), db.joinedload(EventoNegocio.sede))
        if anio:
            consulta = consulta.filter(db.extract("year", EventoNegocio.fecha_inicio) == anio)
        if campana in ("grande", "chica"):
            mes = db.extract("month", EventoNegocio.fecha_inicio)
            consulta = consulta.filter(mes <= 6) if campana == "grande" else consulta.filter(mes >= 7)
        if tipo in TIPOS_EVENTO:
            consulta = consulta.filter(EventoNegocio.tipo == tipo)
        eventos = consulta.order_by(EventoNegocio.fecha_inicio.desc(), EventoNegocio.id_evento.desc()).all()
        anios = sorted({int(a) for (a,) in db.session.query(db.extract("year", EventoNegocio.fecha_inicio)).distinct()}, reverse=True)
    except SQLAlchemyError as error:
        registrar_error_conexion("listar eventos del negocio", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("eventos/listar.html", eventos=[], anios=[], anio=anio, campana=campana, tipo=tipo,
                               tipos=TIPOS_EVENTO, impactos=IMPACTOS), 503
    return render_template("eventos/listar.html", eventos=eventos, anios=anios, anio=anio, campana=campana,
                           tipo=tipo, tipos=TIPOS_EVENTO, impactos=IMPACTOS)


def _formulario(evento, valores, estado=200):
    productos, sedes = _listas()
    return render_template("eventos/formulario.html", evento=evento, valores=valores, productos=productos, sedes=sedes,
                           tipos=TIPOS_EVENTO, impactos=IMPACTOS), estado


@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def nuevo():
    if request.method == "POST":
        try:
            datos, errores = _validar(request.form)
            if not errores:
                evento = EventoNegocio(**datos, id_usuario=current_user.id_usuario)
                db.session.add(evento)
                db.session.commit()
                registrar_accion("evento_creado", "evento", evento.id_evento, evento.descripcion)
                flash("Evento registrado. Se tendrá en cuenta la próxima vez que se actualice la analítica.", "success")
                return redirect(url_for("eventos.listar"))
        except SQLAlchemyError as error:
            db.session.rollback()
            registrar_error_conexion("registrar evento del negocio", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return _formulario(None, request.form, 503)
        for e in errores:
            flash(e, "danger")
        return _formulario(None, request.form)
    return _formulario(None, {"fecha_inicio": date.today().isoformat(), "impacto": "medio"})


@bp.route("/<int:id_evento>/editar", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def editar(id_evento):
    evento = db.session.get(EventoNegocio, id_evento)
    if evento is None:
        flash("El evento no existe.", "danger")
        return redirect(url_for("eventos.listar"))
    if request.method == "POST":
        try:
            datos, errores = _validar(request.form)
            if not errores:
                cambios = describir_cambios(
                    evento, datos,
                    {"tipo": "tipo", "fecha_inicio": "inicio", "fecha_fin": "fin", "descripcion": "descripción",
                     "impacto": "impacto", "id_producto": "producto", "id_sede": "sede",
                     "cantidad_no_atendida": "cantidad no atendida", "es_ejemplo": "es ejemplo"})
                for campo, valor in datos.items():
                    setattr(evento, campo, valor)
                db.session.commit()
                registrar_accion("evento_editado", "evento", evento.id_evento,
                                 f"{evento.descripcion}: {cambios or 'sin cambios'}")
                flash("Evento actualizado.", "success")
                return redirect(url_for("eventos.listar"))
        except SQLAlchemyError as error:
            db.session.rollback()
            registrar_error_conexion("editar evento del negocio", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return _formulario(evento, request.form, 503)
        for e in errores:
            flash(e, "danger")
        return _formulario(evento, request.form)
    return _formulario(evento, {
        "tipo": evento.tipo, "fecha_inicio": evento.fecha_inicio.isoformat(),
        "fecha_fin": evento.fecha_fin.isoformat() if evento.fecha_fin else "",
        "id_producto": evento.id_producto or "", "id_sede": evento.id_sede or "", "descripcion": evento.descripcion,
        "impacto": evento.impacto, "cantidad_no_atendida": evento.cantidad_no_atendida or "", "es_ejemplo": evento.es_ejemplo,
    })


@bp.route("/<int:id_evento>/eliminar", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def eliminar(id_evento):
    evento = db.session.get(EventoNegocio, id_evento)
    if evento is None:
        flash("El evento no existe.", "danger")
        return redirect(url_for("eventos.listar"))
    descripcion = f"{evento.descripcion} ({evento.tipo}, {evento.fecha_inicio:%d/%m/%Y})"
    try:
        db.session.delete(evento)
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        registrar_error_conexion("eliminar evento del negocio", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("eventos.listar"))
    registrar_accion("evento_eliminado", "evento", id_evento, descripcion)
    flash("Evento eliminado.", "success")
    return redirect(url_for("eventos.listar"))
