from datetime import datetime

from app.extensions import db


class Bitacora(db.Model):
    """
    Bitácora de auditoría: deja constancia de quién hizo qué, cuándo y desde
    dónde (seguridad: trazabilidad y responsabilidad de los usuarios).

    Es un registro de solo-agregar: la aplicación nunca actualiza ni borra
    filas de esta tabla, y no existe ninguna pantalla que lo permita.
    usuario_login se guarda como texto aparte de id_usuario a propósito: así
    se conserva el rastro de intentos de acceso con usuarios que no existen
    y de usuarios que luego se desactiven o cambien de nombre.
    """

    __tablename__ = "bitacora_acciones"
    __table_args__ = (
        db.Index("ix_bitacora_fecha", "fecha_hora"),
        db.Index("ix_bitacora_login_accion", "usuario_login", "accion", "fecha_hora"),
        db.Index("ix_bitacora_ip_accion", "ip", "accion", "fecha_hora"),
    )

    id_bitacora = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True, autoincrement=True)
    # hora local del servidor, calculada en Python (no en MySQL) para que la
    # lectura y la escritura usen siempre el mismo reloj
    fecha_hora = db.Column(db.DateTime, nullable=False, default=datetime.now)
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario", ondelete="SET NULL"))
    usuario_login = db.Column(db.String(30))
    accion = db.Column(db.String(40), nullable=False)
    entidad = db.Column(db.String(30))
    id_entidad = db.Column(db.String(30))
    detalle = db.Column(db.String(255))
    ip = db.Column(db.String(45))
    resultado = db.Column(db.String(10), nullable=False, default="exito")  # exito | fallo | denegado

    usuario = db.relationship("Usuario")

    # solo para que se vea algo legible en consola/logs
    def __repr__(self):
        return f"<Bitacora {self.accion} {self.usuario_login} {self.fecha_hora}>"
