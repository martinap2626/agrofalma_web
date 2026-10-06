"""
Mecanismos de seguridad compartidos por toda la aplicación:

- Bitácora de auditoría (registrar_accion): quién hizo qué, cuándo y desde dónde.
- Bloqueo temporal de cuentas por intentos de acceso fallidos (fuerza bruta).
- Política de contraseñas.
- Cabeceras HTTP de seguridad (CSP, anti-clickjacking, etc.).
- Control de propiedad de registros (un Trabajador solo ve lo suyo).

Todo vive en un solo módulo para que las reglas de seguridad se puedan
auditar en un único lugar, en vez de estar repartidas por cada blueprint.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.bitacora import Bitacora

# hash de relleno: se compara contra él cuando el usuario no existe, para que
# el login tarde lo mismo exista o no la cuenta (evita adivinar usuarios
# midiendo el tiempo de respuesta)
_HASH_FALSO = generate_password_hash("relleno-para-igualar-tiempos")

CONTRASENAS_COMUNES = {
    "12345678", "123456789", "1234567890", "password", "password1", "contrasena1",
    "qwerty123", "abc12345", "agrofalma1", "agrofalma123", "admin123", "11111111",
}


# ---------------------------------------------------------------------------
# Bitácora de auditoría
# ---------------------------------------------------------------------------
# agrega una fila a la bitácora; un fallo al auditar nunca debe tumbar la operación del usuario
def registrar_accion(accion, entidad=None, id_entidad=None, detalle=None, resultado="exito",
                     usuario_login=None, id_usuario=None):
    """
    Llamar SIEMPRE después del commit de la operación principal: esta función
    hace su propio commit, y no debe arrastrar cambios a medio guardar.
    """
    try:
        if current_user and current_user.is_authenticated:
            id_usuario = id_usuario or current_user.id_usuario
            usuario_login = usuario_login or current_user.usuario_login

        db.session.add(
            Bitacora(
                id_usuario=id_usuario,
                usuario_login=(usuario_login or None) and usuario_login[:30],
                accion=accion,
                entidad=entidad,
                id_entidad=str(id_entidad) if id_entidad is not None else None,
                detalle=detalle[:255] if detalle else None,
                ip=(request.remote_addr or "")[:45] or None,
                resultado=resultado,
            )
        )
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error("No se pudo escribir en la bitácora (%s): %s", accion, error)


# ---------------------------------------------------------------------------
# Bloqueo por intentos fallidos
# ---------------------------------------------------------------------------
# dice si este usuario (o esta IP) acumuló demasiados intentos fallidos recientes
def acceso_bloqueado(usuario_login, ip):
    """
    Cuenta los 'login_fallido' dentro de la ventana configurada, descartando
    los anteriores al último login exitoso de esa cuenta. Se cuenta por el
    texto escrito (exista o no el usuario), así la respuesta es idéntica
    para cuentas reales y falsas. Además limita por IP, por si alguien prueba
    muchos usuarios distintos desde el mismo equipo.
    """
    maximo = current_app.config["LOGIN_MAX_INTENTOS"]
    inicio = datetime.now() - timedelta(minutes=current_app.config["LOGIN_VENTANA_MIN"])

    try:
        ultimo_ok = (
            db.session.query(db.func.max(Bitacora.fecha_hora))
            .filter(Bitacora.usuario_login == usuario_login, Bitacora.accion == "login_exitoso")
            .scalar()
        )
        desde_usuario = max(inicio, ultimo_ok) if ultimo_ok else inicio

        fallos_usuario = (
            Bitacora.query.filter(
                Bitacora.accion == "login_fallido",
                Bitacora.usuario_login == usuario_login,
                Bitacora.fecha_hora > desde_usuario,
            ).count()
        )
        if fallos_usuario >= maximo:
            return True

        if ip:
            fallos_ip = (
                Bitacora.query.filter(
                    Bitacora.accion == "login_fallido", Bitacora.ip == ip, Bitacora.fecha_hora > inicio
                ).count()
            )
            if fallos_ip >= maximo * 4:
                return True
    except SQLAlchemyError:
        db.session.rollback()
        raise
    return False


# compara la contraseña gastando el mismo tiempo aunque el usuario no exista
def verificar_credenciales(usuario, contrasena):
    if usuario is None:
        check_password_hash(_HASH_FALSO, contrasena)
        return False
    return usuario.check_password(contrasena)


# ---------------------------------------------------------------------------
# Política de contraseñas
# ---------------------------------------------------------------------------
# devuelve la lista de problemas de una contraseña nueva (vacía = es aceptable)
def validar_contrasena(contrasena, usuario_login=""):
    errores = []
    if len(contrasena) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")
    if not any(c.isalpha() for c in contrasena) or not any(c.isdigit() for c in contrasena):
        errores.append("La contraseña debe combinar letras y números.")
    if usuario_login and contrasena.lower() == usuario_login.lower():
        errores.append("La contraseña no puede ser igual al usuario de acceso.")
    if contrasena.lower() in CONTRASENAS_COMUNES:
        errores.append("Esa contraseña es demasiado común; elige otra.")
    return errores


# ---------------------------------------------------------------------------
# Validación de entradas (evita datos que rompan la base de datos o el cálculo)
# ---------------------------------------------------------------------------
MONTO_MAXIMO = Decimal("99999999.99")  # lo que cabe en un NUMERIC(10, 2)


# convierte texto en Decimal finito, entre 0 y el máximo; si no, lanza InvalidOperation
def leer_decimal(texto, maximo=MONTO_MAXIMO):
    """
    Decimal() acepta cosas como "NaN" o "Infinity", que no son montos y que
    hacen fallar las comparaciones o desbordan la columna de la base de datos.
    """
    valor = Decimal(texto)
    if not valor.is_finite() or valor < 0 or valor > maximo:
        raise InvalidOperation
    return valor


# agrega un error si el texto es más largo que la columna donde se va a guardar
def validar_largo(errores, valor, maximo, etiqueta):
    if valor and len(valor) > maximo:
        errores.append(f"{etiqueta} no puede superar los {maximo} caracteres.")


# ---------------------------------------------------------------------------
# Cabeceras HTTP de seguridad
# ---------------------------------------------------------------------------
POLITICA_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "  # el sistema usa atributos style="..." en varias pantallas
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


# agrega las cabeceras de seguridad a cada respuesta
def aplicar_cabeceras(respuesta):
    respuesta.headers.setdefault("Content-Security-Policy", POLITICA_CSP)
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("X-Frame-Options", "DENY")
    respuesta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    respuesta.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    respuesta.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

    if request.is_secure:
        respuesta.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    # que el navegador no guarde páginas con datos del sistema: al cerrar
    # sesión, el botón "atrás" no debe mostrar información de la cuenta
    if request.endpoint != "static" and respuesta.mimetype == "text/html":
        respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


# ---------------------------------------------------------------------------
# Propiedad de registros (control de acceso a nivel de objeto)
# ---------------------------------------------------------------------------
# el Administrador ve todo; el Trabajador solo las ventas que él mismo registró
def puede_ver_venta(venta):
    return current_user.es_administrador or venta.id_usuario == current_user.id_usuario
