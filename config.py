"""
Configuración de la aplicación.

Las credenciales de conexión NUNCA se escriben aquí: se leen desde variables
de entorno (archivo .env, excluido del repositorio mediante .gitignore),
siguiendo la buena práctica descrita en el documento de proyecto.
"""

import os
from dotenv import load_dotenv

# Carga el archivo .env que esté en la raíz del proyecto (si existe)
load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "cambiar-esta-clave-en-produccion")

    DB_USER = os.environ.get("DB_USER", "agrofalma_app")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "3306")
    DB_NAME = os.environ.get("DB_NAME", "agrofalma_bd")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sesión: expira la cookie de sesión cuando el navegador se cierra
    SESSION_PERMANENT = False
