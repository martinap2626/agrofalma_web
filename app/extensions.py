"""
Instancias de extensiones compartidas por toda la aplicación.

Se declaran aquí, sin vincularlas todavía a ninguna app concreta, para
evitar imports circulares entre los distintos módulos (Blueprints).
Se vinculan a la app real dentro de create_app() (ver app/__init__.py).
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Debes iniciar sesión para acceder a esta página."
login_manager.login_message_category = "warning"

# protección CSRF (RNF de seguridad básica): exige un token válido en todo
# POST/PUT/PATCH/DELETE, para que un sitio externo no pueda disparar
# acciones (ej. desactivar un usuario, registrar una venta) usando la
# sesión ya iniciada de un administrador sin que él lo sepa.
csrf = CSRFProtect()
