from app.extensions import db


class ParametroSistema(db.Model):
    """Un ajuste del sistema que el Administrador puede cambiar sin tocar código (ver app/parametros/__init__.py)."""

    __tablename__ = "parametros_sistema"

    clave = db.Column(db.String(40), primary_key=True)
    valor = db.Column(db.String(40), nullable=False)
    actualizado_por = db.Column(db.String(30))
    fecha_actualizacion = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
