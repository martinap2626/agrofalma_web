"""
Módulo de usuarios (Sprint 3, alcance de gestión de cuentas): alta, edición
y activación/desactivación de cuentas de acceso. Todo el módulo es
exclusivo de Administrador (RNF-03), igual que ya lo marca el menú lateral.

El usuario_login no se puede editar una vez creado (evita romper el
historial de accesos), y la contraseña es opcional al editar: si se deja en
blanco, se conserva la actual.
"""

from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.models import Usuario, Rol, Sede
from app.seguridad import registrar_accion, validar_contrasena, validar_largo

bp = Blueprint("usuarios", __name__, url_prefix="/usuarios")


# lista los usuarios del sistema, con buscador por nombre/apellido/usuario
@bp.route("")
@login_required
@rol_requerido("Administrador")
def listar():
    busqueda = request.args.get("q", "").strip()

    try:
        consulta = Usuario.query.options(db.joinedload(Usuario.rol), db.joinedload(Usuario.sede))
        if busqueda:
            patron = f"%{busqueda}%"
            consulta = consulta.filter(
                db.or_(
                    Usuario.nombre.ilike(patron),
                    Usuario.apellido.ilike(patron),
                    Usuario.usuario_login.ilike(patron),
                )
            )
        usuarios = consulta.order_by(Usuario.nombre).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar usuarios", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("usuarios/listar.html", usuarios=[], busqueda=busqueda), 503

    return render_template("usuarios/listar.html", usuarios=usuarios, busqueda=busqueda)


# crea una cuenta de usuario nueva
@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def nuevo():
    try:
        roles = Rol.query.filter_by(estado=True).order_by(Rol.nombre_rol).all()
        sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de nuevo usuario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("usuarios.listar"))

    if request.method == "POST":
        datos, errores = _validar_formulario(request.form, es_nuevo=True)

        if not errores:
            usuario = Usuario(
                nombre=datos["nombre"],
                apellido=datos["apellido"],
                usuario_login=datos["usuario_login"],
                correo=datos["correo"],
                id_rol=datos["id_rol"],
                id_sede=datos["id_sede"],
            )
            usuario.set_password(datos["contrasena"])
            db.session.add(usuario)
            try:
                db.session.commit()
                registrar_accion(
                    "usuario_creado", "usuario", usuario.id_usuario,
                    f"{usuario.usuario_login} (rol {usuario.rol.nombre_rol})",
                )
                flash(f'Usuario "{usuario.usuario_login}" creado correctamente.', "success")
                return redirect(url_for("usuarios.listar"))
            except IntegrityError:
                db.session.rollback()
                errores.append("El usuario o el correo ya están en uso.")
            except SQLAlchemyError as error:
                registrar_error_conexion("crear usuario", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template(
                    "usuarios/formulario.html", roles=roles, sedes=sedes, usuario=None, valores=request.form
                ), 503

        for error in errores:
            flash(error, "danger")
        return render_template("usuarios/formulario.html", roles=roles, sedes=sedes, usuario=None, valores=request.form)

    return render_template("usuarios/formulario.html", roles=roles, sedes=sedes, usuario=None, valores={})


# edita los datos de una cuenta existente
@bp.route("/<int:id_usuario>/editar", methods=["GET", "POST"])
@login_required
@rol_requerido("Administrador")
def editar(id_usuario):
    try:
        usuario = db.get_or_404(Usuario, id_usuario)
        roles = Rol.query.filter_by(estado=True).order_by(Rol.nombre_rol).all()
        sedes = Sede.query.filter_by(estado=True).order_by(Sede.nombre_sede).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir formulario de edición de usuario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("usuarios.listar"))

    if request.method == "POST":
        datos, errores = _validar_formulario(request.form, es_nuevo=False, id_usuario_actual=id_usuario)

        if not errores:
            usuario.nombre = datos["nombre"]
            usuario.apellido = datos["apellido"]
            usuario.correo = datos["correo"]
            usuario.id_rol = datos["id_rol"]
            usuario.id_sede = datos["id_sede"]
            if datos["contrasena"]:
                usuario.set_password(datos["contrasena"])

            try:
                db.session.commit()
                # si el Administrador cambió SU PROPIA contraseña, su sesión actual sigue valida (las demás se cierran)
                if datos["contrasena"] and usuario.id_usuario == current_user.id_usuario:
                    session["sv"] = usuario.version_sesion
                registrar_accion(
                    "usuario_editado", "usuario", usuario.id_usuario,
                    f"{usuario.usuario_login} (rol {usuario.rol.nombre_rol})"
                    + ("; contraseña cambiada" if datos["contrasena"] else ""),
                )
                flash(f'Usuario "{usuario.usuario_login}" actualizado correctamente.', "success")
                return redirect(url_for("usuarios.listar"))
            except IntegrityError:
                db.session.rollback()
                errores.append("El correo ya está en uso por otro usuario.")
            except SQLAlchemyError as error:
                registrar_error_conexion("actualizar usuario", error)
                flash(MENSAJE_ERROR_CONEXION, "danger")
                return render_template(
                    "usuarios/formulario.html", roles=roles, sedes=sedes, usuario=usuario, valores=request.form
                ), 503

        for error in errores:
            flash(error, "danger")
        return render_template("usuarios/formulario.html", roles=roles, sedes=sedes, usuario=usuario, valores=request.form)

    return render_template("usuarios/formulario.html", roles=roles, sedes=sedes, usuario=usuario, valores=None)


# desactiva una cuenta para que no pueda volver a entrar (no se borra)
@bp.route("/<int:id_usuario>/desactivar", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def desactivar(id_usuario):
    if id_usuario == current_user.id_usuario:
        flash("No puedes desactivar tu propia cuenta.", "danger")
        return redirect(url_for("usuarios.listar"))

    try:
        usuario = db.get_or_404(Usuario, id_usuario)
        usuario.estado = False
        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("desactivar usuario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("usuarios.listar"))

    registrar_accion("usuario_desactivado", "usuario", usuario.id_usuario, usuario.usuario_login)
    flash(f'Usuario "{usuario.usuario_login}" desactivado.', "success")
    return redirect(url_for("usuarios.listar"))


# reactiva una cuenta que estaba desactivada
@bp.route("/<int:id_usuario>/reactivar", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def reactivar(id_usuario):
    try:
        usuario = db.get_or_404(Usuario, id_usuario)
        usuario.estado = True
        db.session.commit()
    except SQLAlchemyError as error:
        registrar_error_conexion("reactivar usuario", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("usuarios.listar"))

    registrar_accion("usuario_reactivado", "usuario", usuario.id_usuario, usuario.usuario_login)
    flash(f'Usuario "{usuario.usuario_login}" reactivado.', "success")
    return redirect(url_for("usuarios.listar"))


# ---------------------------------------------------------------------------
# Validación server-side
# ---------------------------------------------------------------------------
# valida los datos del formulario de usuario antes de crear o editar
def _validar_formulario(form, es_nuevo, id_usuario_actual=None):
    errores = []

    nombre = form.get("nombre", "").strip()
    apellido = form.get("apellido", "").strip()
    usuario_login = form.get("usuario_login", "").strip()
    correo = form.get("correo", "").strip()
    id_rol_raw = form.get("id_rol", "")
    id_sede_raw = form.get("id_sede", "")
    contrasena = form.get("contrasena", "")
    confirmar_contrasena = form.get("confirmar_contrasena", "")

    validar_largo(errores, nombre, 50, "El nombre")
    validar_largo(errores, apellido, 50, "El apellido")
    validar_largo(errores, usuario_login, 30, "El usuario de acceso")
    validar_largo(errores, correo, 100, "El correo")
    if not nombre:
        errores.append("El nombre es obligatorio.")
    if not apellido:
        errores.append("El apellido es obligatorio.")

    if es_nuevo:
        if not usuario_login:
            errores.append("El usuario de acceso es obligatorio.")
        elif Usuario.query.filter_by(usuario_login=usuario_login).first():
            errores.append("Ese usuario de acceso ya existe.")

    if not id_rol_raw:
        errores.append("Debes seleccionar un rol.")
    if not id_sede_raw:
        errores.append("Debes seleccionar una sede.")

    if es_nuevo and not contrasena:
        errores.append("La contraseña es obligatoria.")
    if contrasena:
        errores.extend(validar_contrasena(contrasena, usuario_login))
    if contrasena and contrasena != confirmar_contrasena:
        errores.append("Las contraseñas no coinciden.")

    datos = {
        "nombre": nombre,
        "apellido": apellido,
        "usuario_login": usuario_login,
        "correo": correo or None,
        "id_rol": int(id_rol_raw) if id_rol_raw else None,
        "id_sede": int(id_sede_raw) if id_sede_raw else None,
        "contrasena": contrasena,
    }
    return datos, errores
