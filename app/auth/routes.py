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
from app.extensions import db
from app.models import Usuario
from app.seguridad import (
    acceso_bloqueado, acciones_recientes, registrar_accion, validar_contrasena, verificar_credenciales,
)

bp = Blueprint("auth", __name__, url_prefix="")

# cambio de contraseña propio: intentos fallidos con la contraseña actual permitidos por ventana de tiempo
MAX_FALLOS_CONTRASENA = 5
VENTANA_FALLOS_MIN = 15


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
        # versión de sesión: si luego se cambia la contraseña, esta sesión deja de ser válida
        session["sv"] = usuario.version_sesion
        # sesión "permanente" = caduca por INACTIVIDAD (config PERMANENT_SESSION_LIFETIME),
        # no cuando se cierra el navegador
        session.permanent = True
        usuario.registrar_acceso()
        registrar_accion("login_exitoso", id_usuario=usuario.id_usuario, usuario_login=usuario.usuario_login)
        return redirect(destino_inicio(usuario))

    return render_template("auth/login.html")


# cierra la sesion y manda de vuelta al login. Solo por POST (con token CSRF): así ninguna otra página puede cerrar la
# sesión del usuario con un simple enlace o una imagen. Una visita por GET (por ejemplo un favorito viejo) no cierra nada.
@bp.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "GET":
        return redirect(url_for("index"))
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    registrar_accion("logout")
    logout_user()
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("auth.login"))


# el propio usuario cambia su contraseña (pide la actual); al cambiarla, las demás sesiones abiertas se cierran
@bp.route("/cuenta/contrasena", methods=["GET", "POST"])
@login_required
def cambiar_contrasena():
    if request.method == "POST":
        actual = request.form.get("contrasena_actual", "")
        nueva = request.form.get("contrasena_nueva", "")
        confirmar = request.form.get("confirmar_contrasena", "")

        # límite de intentos: sin él, alguien con la sesión abierta de otro podría adivinar su contraseña actual
        if acciones_recientes("contrasena_fallida", current_user.id_usuario, VENTANA_FALLOS_MIN) >= MAX_FALLOS_CONTRASENA:
            registrar_accion("contrasena_bloqueada", resultado="denegado", detalle="Demasiados intentos con la contraseña actual")
            flash("Demasiados intentos con la contraseña actual. Espera unos minutos e inténtalo de nuevo.", "danger")
            return render_template("auth/cambiar_contrasena.html"), 429

        errores = []
        if not current_user.check_password(actual):
            errores.append("La contraseña actual no es correcta.")
            registrar_accion("contrasena_fallida", resultado="fallo")
        else:
            errores.extend(validar_contrasena(nueva, current_user.usuario_login))
            if nueva and nueva == actual:
                errores.append("La contraseña nueva debe ser distinta de la actual.")
            if nueva != confirmar:
                errores.append("La confirmación no coincide con la contraseña nueva.")

        if not errores:
            try:
                current_user.set_password(nueva)
                db.session.commit()
            except SQLAlchemyError as error:
                db.session.rollback()
                registrar_error_conexion("cambiar contraseña", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template("auth/cambiar_contrasena.html"), 503
            # esta sesión sigue válida; las demás (otros equipos o navegadores) quedan cerradas
            session["sv"] = current_user.version_sesion
            registrar_accion("contrasena_cambiada", "usuario", current_user.id_usuario, "Cambio de contraseña propio")
            flash("Contraseña actualizada. Las demás sesiones abiertas con tu cuenta se cerraron.", "success")
            return redirect(destino_inicio(current_user))

        for mensaje in errores:
            flash(mensaje, "danger")
        return render_template("auth/cambiar_contrasena.html"), 400

    return render_template("auth/cambiar_contrasena.html")
