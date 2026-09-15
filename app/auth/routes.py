"""
Módulo de autenticación — HU-01: Iniciar sesión según rol asignado.

Criterios de aceptación cubiertos aquí (ver Product Backlog):
- Credenciales válidas y usuario activo -> redirige según el rol.
- Credenciales inválidas -> mensaje de error genérico (no indica cuál
  dato falló, para no dar pistas a un atacante).
- Usuario inactivo -> acceso denegado con mensaje explícito.
"""

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import SQLAlchemyError

from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.models import Usuario

bp = Blueprint("auth", __name__, url_prefix="")


# decide a que pantalla mandar al usuario despues de loguearse, segun su rol
def destino_inicio(usuario):
    """
    Página de aterrizaje según el rol: el Administrador ve el dashboard
    comercial agregado (HU-07); el Trabajador ve su propia actividad del
    día (ventas.mis_ventas ya cumple ese rol, sin duplicar pantalla).
    """
    if usuario.es_administrador:
        return url_for("dashboard.inicio")
    return url_for("ventas.mis_ventas")


# pantalla de login: valida usuario/contraseña y redirige segun el rol
@bp.route("/login", methods=["GET", "POST"])
def login():
    # Si ya hay una sesión activa, no tiene sentido volver a mostrar el login
    if current_user.is_authenticated:
        return redirect(destino_inicio(current_user))

    if request.method == "POST":
        usuario_login = request.form.get("usuario", "").strip()
        contrasena = request.form.get("contrasena", "")

        try:
            usuario = Usuario.query.filter_by(usuario_login=usuario_login).first()
        except SQLAlchemyError as error:
            registrar_error_conexion("login", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template("auth/login.html"), 503

        # Mensaje de error único e genérico: no revela si falló el
        # usuario, la contraseña o el estado de la cuenta.
        credenciales_invalidas = (
            usuario is None or not usuario.check_password(contrasena)
        )

        if credenciales_invalidas:
            flash("Usuario o contraseña incorrectos.", "danger")
            return render_template("auth/login.html"), 401

        if not usuario.estado:
            flash("Esta cuenta se encuentra desactivada. Contacta al administrador.", "danger")
            return render_template("auth/login.html"), 403

        login_user(usuario)
        usuario.registrar_acceso()
        return redirect(destino_inicio(usuario))

    return render_template("auth/login.html")


# cierra la sesion y manda de vuelta al login
@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("auth.login"))
