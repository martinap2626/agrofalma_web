"""Pantalla «Parámetros»: el Administrador ajusta los valores del cálculo de abastecimiento y el tope de descuento."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
from app.parametros import DEFINICIONES, ETIQUETAS, guardar, validar, valores
from app.seguridad import describir_cambios, registrar_accion

bp = Blueprint("parametros", __name__, url_prefix="/parametros")


class _Valores:
    """Permite que describir_cambios lea los valores actuales como atributos."""

    def __init__(self, datos):
        self.__dict__.update(datos)


@bp.route("", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def inicio():
    if request.method == "POST":
        nuevos, errores = validar(request.form)
        if errores:
            for e in errores:
                flash(e, "danger")
            return render_template("parametros/inicio.html", definiciones=DEFINICIONES, actuales=request.form), 400
        try:
            cambios = describir_cambios(_Valores(valores()), nuevos, ETIQUETAS)
            if not cambios:
                flash("No hay cambios que guardar.", "info")
                return redirect(url_for("parametros.inicio"))
            guardar(nuevos, current_user.usuario_login)
            db.session.commit()
        except SQLAlchemyError as error:
            db.session.rollback()
            registrar_error_conexion("guardar parámetros del sistema", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template("parametros/inicio.html", definiciones=DEFINICIONES, actuales=request.form), 503
        registrar_accion("parametros_editados", "parametros", None, cambios)
        flash("Parámetros guardados.", "success")
        return redirect(url_for("parametros.inicio"))

    try:
        actuales = valores()
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir parámetros del sistema", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("dashboard.inicio"))
    return render_template("parametros/inicio.html", definiciones=DEFINICIONES, actuales=actuales)
