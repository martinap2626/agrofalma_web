from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


class Usuario(UserMixin, db.Model):
    """
    Cuenta de acceso al sistema. Implementa UserMixin para integrarse
    con Flask-Login (RF-01: inicio de sesión según rol asignado).
    """

    __tablename__ = "usuarios"

    id_usuario = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    apellido = db.Column(db.String(50), nullable=False)
    usuario_login = db.Column(db.String(30), nullable=False, unique=True)
    contrasena_hash = db.Column(db.String(255), nullable=False)
    correo = db.Column(db.String(100), unique=True)
    id_rol = db.Column(db.Integer, db.ForeignKey("roles.id_rol"), nullable=False)
    id_sede = db.Column(db.Integer, db.ForeignKey("sedes.id_sede"), nullable=False)
    estado = db.Column(db.Boolean, nullable=False, default=True)
    fecha_creacion = db.Column(db.DateTime, server_default=db.func.now())
    ultimo_acceso = db.Column(db.DateTime)
    # sube cada vez que cambia la contraseña: las sesiones abiertas con un valor anterior dejan de ser válidas
    # (así una contraseña cambiada por sospecha de robo cierra también la sesión del ladrón)
    version_sesion = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    rol = db.relationship("Rol", back_populates="usuarios")
    sede = db.relationship("Sede", back_populates="usuarios")

    # --- Integración con Flask-Login -------------------------------------
    # flask-login necesita este metodo y que devuelva un string, no un int
    def get_id(self):
        # Flask-Login exige que get_id() devuelva un string
        return str(self.id_usuario)

    # si el usuario esta desactivado, no se le deja quedar logueado aunque el login haya salido bien
    @property
    def is_active(self):
        # Sobrescribe UserMixin: un usuario inactivo (estado=0) no puede
        # mantener sesión aunque el login sea correcto (RNF-03).
        return bool(self.estado)

    # --- Contraseña --------------------------------------------------------
    # genera el hash de la contraseña, nunca se guarda en texto plano
    def set_password(self, contrasena_plana):
        self.contrasena_hash = generate_password_hash(contrasena_plana)
        # cambiar la contraseña invalida las sesiones abiertas anteriormente (ver version_sesion)
        self.version_sesion = (self.version_sesion or 0) + 1

    # compara la contraseña escrita en el login contra el hash guardado
    def check_password(self, contrasena_plana):
        return check_password_hash(self.contrasena_hash, contrasena_plana)

    # --- Utilidades de rol ---------------------------------------------
    # true si el rol de este usuario es Administrador
    @property
    def es_administrador(self):
        return self.rol.nombre_rol == "Administrador"

    # junta nombre y apellido para mostrarlo en la interfaz
    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}"

    # guarda la fecha/hora de la ultima vez que este usuario inicio sesion
    def registrar_acceso(self):
        # hora local del servidor, igual que la bitácora (datetime.utcnow está obsoleto desde Python 3.12)
        self.ultimo_acceso = datetime.now()
        db.session.commit()

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Usuario {self.usuario_login}>"
