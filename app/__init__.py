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
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import SQLAlchemyError

from config import Config
from app.extensions import db, login_manager, csrf


# arma la app completa: conecta las extensiones, registra los blueprints y define los manejadores de error
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # La clave de firma de sesiones y tokens CSRF debe venir del entorno (.env).
    # Sin ella, cualquiera que conozca el valor por defecto podría falsificar
    # sesiones, así que la app prefiere no arrancar.
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "Falta SECRET_KEY. Defínela en el archivo .env (ver .env.example) con un valor largo y aleatorio."
        )
    if len(app.config["SECRET_KEY"]) < 16 and not app.config.get("TESTING"):
        app.logger.warning("SECRET_KEY es corta (menos de 16 caracteres): usa un valor largo y aleatorio en producción.")

    if app.config.get("CONFIAR_EN_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    _configurar_log_archivo(app)

    # --- Extensiones ---------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # --- Blueprints ------------------------------------------------------
    from app.auth import bp as auth_bp
    from app.productos import bp as productos_bp
    from app.ventas import bp as ventas_bp
    from app.usuarios import bp as usuarios_bp
    from app.dashboard import bp as dashboard_bp
    from app.reportes import bp as reportes_bp
    from app.analitica import bp as analitica_bp
    from app.auditoria import bp as auditoria_bp
    from app.ayuda import bp as ayuda_bp
    from app.eventos import bp as eventos_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(productos_bp)
    app.register_blueprint(ventas_bp)
    app.register_blueprint(usuarios_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reportes_bp)
    app.register_blueprint(analitica_bp)
    app.register_blueprint(auditoria_bp)
    app.register_blueprint(ayuda_bp)
    app.register_blueprint(eventos_bp)

    # --- Cabeceras HTTP de seguridad en todas las respuestas ---------------
    from app.seguridad import aplicar_cabeceras

    app.after_request(aplicar_cabeceras)

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

    # pantalla de "no tienes permiso" cuando alguien entra a algo que no le toca;
    # además queda en la bitácora, porque un intento de acceso indebido es un evento de seguridad
    @app.errorhandler(403)
    def acceso_prohibido(_e):
        from flask import render_template, request
        from app.seguridad import registrar_accion

        registrar_accion("acceso_denegado", detalle=f"{request.method} {request.path}", resultado="denegado")
        return render_template("errores/403.html"), 403

    # ruta que no existe
    @app.errorhandler(404)
    def no_encontrado(_e):
        from flask import render_template

        return render_template("errores/404.html"), 404

    # el token CSRF falta, venció o no coincide: se pide recargar el formulario en vez de mostrar un error técnico
    @app.errorhandler(CSRFError)
    def csrf_invalido(error):
        from flask import render_template, request
        from app.seguridad import registrar_accion

        registrar_accion("csrf_rechazado", detalle=f"{request.method} {request.path}: {error.description}", resultado="denegado")
        return render_template("errores/400.html"), 400

    # cualquier error inesperado del servidor: pantalla amable, y el detalle técnico queda en el log
    @app.errorhandler(500)
    def error_interno(error):
        from flask import g, render_template

        db.session.rollback()
        app.logger.exception("Error interno no controlado: %s", error)
        # si la propia carga del usuario falló, que la plantilla de error no vuelva a consultar la BD
        g._login_user = login_manager.anonymous_user()
        return render_template("errores/500.html"), 500

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

    # --- Comandos CLI: seed-passwords, respaldar-bd (RNF-06) --------------
    _registrar_comandos_cli(app)

    return app


# deja los errores de la aplicación en un archivo rotativo (logs/), para poder diagnosticar fallos pasados
def _configurar_log_archivo(app):
    import logging
    import os
    from logging.handlers import RotatingFileHandler

    ruta = app.config.get("LOG_ARCHIVO")
    if not ruta or app.config.get("TESTING"):
        return

    try:
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        manejador = RotatingFileHandler(ruta, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    except OSError as error:
        app.logger.warning("No se pudo abrir el archivo de log %s: %s", ruta, error)
        return

    manejador.setLevel(logging.INFO)
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    app.logger.addHandler(manejador)
    app.logger.setLevel(logging.INFO)


# agrupa los comandos de consola propios de la app: seed-passwords, respaldar-bd
def _registrar_comandos_cli(app):
    from app.comandos_respaldo import registrar_comando_respaldo

    registrar_comando_respaldo(app)

    from app.comandos_analitica import registrar_comandos_analitica

    registrar_comandos_analitica(app)

    from app.comandos_historico import registrar_comando_historico

    registrar_comando_historico(app)

    from app.comandos_historico import registrar_comandos_eventos_ejemplo

    registrar_comandos_eventos_ejemplo(app)

    from app.comandos_despliegue import registrar_comandos_despliegue

    registrar_comandos_despliegue(app)

    # crea las tablas nuevas de seguridad (bitácora) sin tocar las existentes
    @app.cli.command("crear-tablas-seguridad")
    def crear_tablas_seguridad():
        """Crea la tabla bitacora_acciones si todavía no existe (es seguro ejecutarlo varias veces)."""
        from app.models import Bitacora

        Bitacora.__table__.create(bind=db.engine, checkfirst=True)
        click.echo("Tabla bitacora_acciones lista.")

    # crea los índices definidos en los modelos que todavía no existan en la base ya creada
    @app.cli.command("crear-indices")
    def crear_indices():
        """Aplica a la base de datos existente los índices declarados en los modelos (idempotente)."""
        from sqlalchemy import inspect

        inspector = inspect(db.engine)
        creados = 0
        for tabla in db.metadata.sorted_tables:
            if not inspector.has_table(tabla.name):
                continue
            existentes = {i["name"] for i in inspector.get_indexes(tabla.name)}
            for indice in tabla.indexes:
                if indice.name not in existentes:
                    indice.create(bind=db.engine)
                    creados += 1
                    click.echo(f"  creado {indice.name} en {tabla.name}")
        click.echo(f"Índices creados: {creados}." if creados else "Todos los índices ya existían.")

    # le pone una contraseña real a los usuarios de prueba que vienen con el hash pendiente
    @app.cli.command("seed-passwords")
    @click.option("--password", default=None, help="Contraseña a asignar. Si se omite, se genera una aleatoria.")
    def seed_passwords(password):
        """
        Asigna una contraseña real (con hash) a los usuarios que trae el
        script agrofalma_datos_prueba.sql con el valor de relleno
        'PENDIENTE_HASH_REAL'. Solo pensado para el entorno de
        desarrollo/demo. Sin --password, genera una contraseña aleatoria
        (no existe ninguna contraseña por defecto conocida).
        """
        import secrets

        from app.models import Usuario
        from app.seguridad import validar_contrasena

        usuarios = Usuario.query.filter_by(contrasena_hash="PENDIENTE_HASH_REAL").all()

        if not usuarios:
            click.echo("No se encontraron usuarios con contraseña pendiente. ¿Ya se ejecutó este comando antes?")
            return

        if password is None:
            password = secrets.token_urlsafe(9) + "7a"
        problemas = validar_contrasena(password)
        if problemas:
            raise click.ClickException(" ".join(problemas))

        for usuario in usuarios:
            usuario.set_password(password)

        db.session.commit()
        click.echo(f"Contraseña '{password}' asignada a {len(usuarios)} usuario(s):")
        for usuario in usuarios:
            click.echo(f"  - {usuario.usuario_login} ({usuario.rol.nombre_rol})")
