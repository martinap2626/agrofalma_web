"""
Manejo compartido de errores de conexión a la base de datos, usado por
todos los blueprints (auth, productos, ventas, usuarios) para mostrarle al
usuario un mensaje amigable en vez de un traceback técnico en inglés.
"""

from flask import current_app

from app.extensions import db

MENSAJE_ERROR_CONEXION = "No se pudo conectar con la base de datos. Por favor, inténtalo de nuevo en unos minutos."


# hace rollback y deja el error en el log, para no repetir esto en cada vista
def registrar_error_conexion(contexto, error):
    db.session.rollback()
    current_app.logger.error("Error de conexión a la base de datos (%s): %s", contexto, error)
