"""
Application factory. Se usa el patrón "application factory" en vez de
una instancia global de Flask porque es el que permite registrar la
aplicación por Blueprints de forma limpia — la misma decisión de
arquitectura (monolito modular) que se sustentó en el proyecto de
tesis: cada módulo (auth, productos, y en sprints siguientes ventas,
analítica, etc.) queda aislado en su propio paquete, pero todos se
ejecutan dentro de una única aplicación.
"""

import click
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from config import Config
from app.extensions import db, login_manager


# arma la app completa: conecta las extensiones, registra los blueprints y define los manejadores de error
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # --- Extensiones ---------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)

    # --- Blueprints ------------------------------------------------------
    from app.auth import bp as auth_bp
    from app.productos import bp as productos_bp
    from app.ventas import bp as ventas_bp
    from app.usuarios import bp as usuarios_bp
    from app.dashboard import bp as dashboard_bp
    from app.reportes import bp as reportes_bp
    from app.analitica import bp as analitica_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(productos_bp)
    app.register_blueprint(ventas_bp)
    app.register_blueprint(usuarios_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reportes_bp)
    app.register_blueprint(analitica_bp)

    # --- Ruta raíz: redirige al dashboard de cada rol (o al login) --------
    # manda a cada quien a su pantalla segun si esta logueado y su rol
    @app.route("/")
    def index():
        from flask import redirect, url_for
        from flask_login import current_user

        from app.auth.routes import destino_inicio

        if current_user.is_authenticated:
            return redirect(destino_inicio(current_user))
        return redirect(url_for("auth.login"))

    # --- user_loader de Flask-Login ---------------------------------------
    from app.models import Usuario

    # le dice a flask-login como recuperar el usuario a partir del id guardado en la sesion
    @login_manager.user_loader
    def load_user(id_usuario):
        return db.session.get(Usuario, int(id_usuario))

    # --- Manejadores de error simples (401 / 403) -------------------------
    # si no hay sesion activa, manda al login en vez de mostrar un error feo
    @app.errorhandler(401)
    def no_autenticado(_e):
        from flask import redirect, url_for
        return redirect(url_for("auth.login"))

    # pantalla de "no tienes permiso" cuando alguien entra a algo que no le toca
    @app.errorhandler(403)
    def acceso_prohibido(_e):
        from flask import render_template
        return render_template("errores/403.html"), 403

    # --- Base de datos no disponible ---------------------------------------
    # Red de seguridad para cualquier consulta (no solo el login): si la
    # base de datos está caída o no responde, se muestra un mensaje
    # amigable en español en vez de un traceback técnico en inglés o una
    # caída del sistema. El detalle técnico real queda en los logs.
    # red de seguridad: si la bd se cae, muestra un mensaje amigable en vez de un error tecnico
    @app.errorhandler(SQLAlchemyError)
    def error_base_datos(error):
        from flask import g, render_template
        db.session.rollback()
        app.logger.error("Error de base de datos: %s", error)

        # Si la base de datos falló justo al intentar cargar el usuario de
        # la sesión (Flask-Login), evitamos que la propia plantilla de
        # error dispare una segunda consulta (para saber quién es
        # current_user) y termine fallando también.
        g._login_user = login_manager.anonymous_user()

        return render_template("errores/servicio_no_disponible.html"), 503

    # --- Comando CLI: asigna contraseñas reales a los usuarios sembrados --
    _registrar_comandos_cli(app)

    return app


# agrupa los comandos de consola propios de la app (por ahora, solo seed-passwords)
def _registrar_comandos_cli(app):
    # le pone una contraseña real a los usuarios de prueba que vienen con el hash pendiente
    @app.cli.command("seed-passwords")
    @click.option("--password", default="Agrofalma2026", help="Contraseña a asignar a todos los usuarios sembrados.")
    def seed_passwords(password):
        """
        Asigna una contraseña real (con hash) a los usuarios que trae el
        script agrofalma_datos_prueba.sql con el valor de relleno
        'PENDIENTE_HASH_REAL'. Solo pensado para el entorno de
        desarrollo/demo del Sprint 1.
        """
        from app.models import Usuario

        usuarios = Usuario.query.filter_by(contrasena_hash="PENDIENTE_HASH_REAL").all()

        if not usuarios:
            click.echo("No se encontraron usuarios con contraseña pendiente. ¿Ya se ejecutó este comando antes?")
            return

        for usuario in usuarios:
            usuario.set_password(password)

        db.session.commit()
        click.echo(f"Contraseña '{password}' asignada a {len(usuarios)} usuario(s):")
        for usuario in usuarios:
            click.echo(f"  - {usuario.usuario_login} ({usuario.rol.nombre_rol})")
