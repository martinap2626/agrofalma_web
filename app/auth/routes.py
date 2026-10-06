"""
Módulo de autenticación — HU-01: Iniciar sesión según rol asignado.

Criterios de aceptación cubiertos aquí (ver Product Backlog):
- Credenciales válidas y usuario activo -> redirige según el rol.
- Credenciales inválidas -> mensaje de error genérico (no indica cuál
  dato falló, para no dar pistas a un atacante).
- Usuario inactivo -> acceso denegado con mensaje explícito.
"""

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import SQLAlchemyError

from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.models import Usuario
from app.seguridad import acceso_bloqueado, registrar_accion, verificar_credenciales

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
        usuario_login = request.form.get("usuario", "").strip()[:30]
        contrasena = request.form.get("contrasena", "")

        try:
            # primero el bloqueo: una cuenta (o IP) con demasiados intentos fallidos
            # recientes no puede entrar ni con la contraseña correcta
            if acceso_bloqueado(usuario_login, request.remote_addr):
                registrar_accion(
                    "login_bloqueado", usuario_login=usuario_login, resultado="denegado",
                    detalle="Demasiados intentos fallidos recientes",
                )
                flash(
                    "Demasiados intentos fallidos. Por seguridad, el acceso se bloqueó temporalmente: "
                    "espera unos minutos o pide ayuda al administrador.",
                    "danger",
                )
                return render_template("auth/login.html"), 429

            usuario = Usuario.query.filter_by(usuario_login=usuario_login).first()
        except SQLAlchemyError as error:
            registrar_error_conexion("login", error)
            flash(MENSAJE_ERROR_CONEXION, "danger")
            return render_template("auth/login.html"), 503

        # Mensaje de error único e genérico: no revela si falló el
        # usuario, la contraseña o el estado de la cuenta.
        credenciales_invalidas = not verificar_credenciales(usuario, contrasena)

        if credenciales_invalidas:
            registrar_accion(
                "login_fallido", usuario_login=usuario_login, resultado="fallo",
                id_usuario=usuario.id_usuario if usuario else None,
            )
            flash("Usuario o contraseña incorrectos.", "danger")
            return render_template("auth/login.html"), 401

        if not usuario.estado:
            registrar_accion(
                "login_cuenta_inactiva", usuario_login=usuario_login, resultado="denegado",
                id_usuario=usuario.id_usuario,
            )
            flash("Esta cuenta se encuentra desactivada. Contacta al administrador.", "danger")
            return render_template("auth/login.html"), 403

        # evita fijación de sesión: se descarta cualquier sesión previa antes de iniciar la nueva
        session.clear()
        login_user(usuario)
        # sesión "permanente" = caduca por INACTIVIDAD (config PERMANENT_SESSION_LIFETIME),
        # no cuando se cierra el navegador
        session.permanent = True
        usuario.registrar_acceso()
        registrar_accion("login_exitoso", id_usuario=usuario.id_usuario, usuario_login=usuario.usuario_login)
        return redirect(destino_inicio(usuario))

    return render_template("auth/login.html")


# cierra la sesion y manda de vuelta al login
@bp.route("/logout")
@login_required
def logout():
    registrar_accion("logout")
    logout_user()
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("auth.login"))
