"""
Configuración de la aplicación.

Las credenciales de conexión NUNCA se escriben aquí: se leen desde variables
de entorno (archivo .env, excluido del repositorio mediante .gitignore),
siguiendo la buena práctica descrita en el documento de proyecto.
"""

import os
from datetime import timedelta

from dotenv import load_dotenv

# Carga el archivo .env que esté en la raíz del proyecto (si existe)
load_dotenv()


# lee una variable de entorno booleana ("1", "true", "si" = verdadero)
def _env_bool(nombre, por_defecto=False):
    valor = os.environ.get(nombre)
    if valor is None:
        return por_defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes")


class Config:
    # Sin valor por defecto a propósito: si falta, la app se niega a arrancar
    # (ver create_app) en vez de funcionar con una clave conocida públicamente.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    DB_USER = os.environ.get("DB_USER", "agrofalma_app")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "3306")
    DB_NAME = os.environ.get("DB_NAME", "agrofalma_bd")

    # Cuentas de MySQL de uso aparte de la aplicación (opcionales; si faltan, se usa DB_USER/DB_PASSWORD):
    #   DB_RESPALDO_*  solo lectura, para el volcado (mysqldump)
    #   DB_ADMIN_*     administración: restaurar, simulacro de recuperación, cambios de estructura y pruebas
    DB_RESPALDO_USER = os.environ.get("DB_RESPALDO_USER") or DB_USER
    DB_RESPALDO_PASSWORD = os.environ.get("DB_RESPALDO_PASSWORD") if os.environ.get("DB_RESPALDO_USER") else DB_PASSWORD
    DB_ADMIN_USER = os.environ.get("DB_ADMIN_USER") or DB_USER
    DB_ADMIN_PASSWORD = os.environ.get("DB_ADMIN_PASSWORD") if os.environ.get("DB_ADMIN_USER") else DB_PASSWORD

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # revisa que la conexión siga viva antes de usarla y la renueva cada 30 min,
    # para que un MySQL reiniciado o con conexiones inactivas cortadas no
    # produzca errores al usuario
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 1800}

    # --- Sesión ---------------------------------------------------------
    # La cookie no es accesible desde JavaScript, no viaja en peticiones
    # entre sitios, y solo viaja por HTTPS cuando SESSION_COOKIE_SECURE=true
    # (activarlo en el servidor de producción, que debe usar HTTPS).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    # la sesión caduca tras N minutos SIN actividad (se renueva en cada petición)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESION_INACTIVIDAD_MIN", "30")))

    # --- Bloqueo por intentos de acceso fallidos --------------------------
    LOGIN_MAX_INTENTOS = int(os.environ.get("LOGIN_MAX_INTENTOS", "5"))
    LOGIN_VENTANA_MIN = int(os.environ.get("LOGIN_VENTANA_MIN", "15"))

    # --- Detrás de un proxy inverso (nginx, etc.) --------------------------
    # solo activar si de verdad hay un proxy de confianza delante; si no, un
    # cliente podría falsear su IP con la cabecera X-Forwarded-For
    CONFIAR_EN_PROXY = _env_bool("CONFIAR_EN_PROXY", False)

    # --- Registro de errores en archivo -------------------------------------
    LOG_ARCHIVO = os.environ.get("LOG_ARCHIVO", "logs/agrofalma.log")

    # --- Control de precios ---------------------------------------------------
    # descuento máximo (en %) sobre el precio de lista que un Trabajador puede dar sin autorización; más que eso solo
    # lo puede hacer el Administrador. Toda venta por debajo del precio de lista queda en la bitácora.
    DESCUENTO_MAX_TRABAJADOR_PCT = float(os.environ.get("DESCUENTO_MAX_TRABAJADOR_PCT", "10"))

    # --- Límite de exportaciones --------------------------------------------
    # máximo de reportes CSV que un usuario puede descargar en la ventana de tiempo (frena la copia masiva de datos)
    EXPORT_MAX_POR_VENTANA = int(os.environ.get("EXPORT_MAX_POR_VENTANA", "10"))
    EXPORT_VENTANA_MIN = int(os.environ.get("EXPORT_VENTANA_MIN", "10"))

    # --- Respaldos de la base de datos --------------------------------------
    # clave para cifrar los respaldos (si está vacía, se guardan sin cifrar); y carpeta FUERA del equipo (disco externo,
    # carpeta sincronizada con la nube, unidad de red) donde se deja una segunda copia de cada respaldo
    RESPALDO_CLAVE = os.environ.get("RESPALDO_CLAVE", "")
    RESPALDO_COPIA_DIR = os.environ.get("RESPALDO_COPIA_DIR", "")
