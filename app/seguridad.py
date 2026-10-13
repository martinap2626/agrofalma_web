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

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.bitacora import Bitacora, BitacoraCadena

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
HUELLA_INICIAL = "0" * 64  # "huella anterior" de la primera fila de la cadena


# huella (SHA-256) de una fila: depende de TODO su contenido y de la huella de la fila anterior
def calcular_huella(previo, fila):
    partes = [
        previo,
        fila.fecha_hora.strftime("%Y-%m-%d %H:%M:%S"),
        str(fila.id_usuario) if fila.id_usuario is not None else "",
        fila.usuario_login or "",
        fila.accion or "",
        fila.entidad or "",
        fila.id_entidad or "",
        fila.detalle or "",
        fila.ip or "",
        fila.resultado or "",
    ]
    return hashlib.sha256("\x1f".join(partes).encode("utf-8")).hexdigest()


# trae (y bloquea hasta el commit) la fila que guarda la huella de la última entrada de la bitácora
def _bloquear_cadena():
    cadena = BitacoraCadena.query.filter_by(id=1).with_for_update().populate_existing().first()
    if cadena is None:
        cadena = BitacoraCadena(id=1, ultimo_hash=HUELLA_INICIAL, total=0)
        db.session.add(cadena)
        db.session.flush()
    return cadena


# agrega una fila a la bitácora; un fallo al auditar nunca debe tumbar la operación del usuario
def registrar_accion(accion, entidad=None, id_entidad=None, detalle=None, resultado="exito",
                     usuario_login=None, id_usuario=None):
    """
    Llamar SIEMPRE después del commit de la operación principal: esta función
    hace su propio commit, y no debe arrastrar cambios a medio guardar.

    Cada fila se encadena con la anterior mediante una huella (ver Bitacora). La fila de la cadena se bloquea mientras
    se agrega el eslabón, de modo que dos acciones simultáneas quedan una detrás de otra y la cadena no se bifurca.
    """
    for intento in (1, 2):
        try:
            if current_user and current_user.is_authenticated:
                id_usuario = id_usuario or current_user.id_usuario
                usuario_login = usuario_login or current_user.usuario_login

            fila = Bitacora(
                # sin fracciones de segundo: MySQL las redondea y la huella no coincidiría al verificarla
                fecha_hora=datetime.now().replace(microsecond=0),
                id_usuario=id_usuario,
                usuario_login=(usuario_login or None) and usuario_login[:30],
                accion=accion,
                entidad=entidad,
                id_entidad=str(id_entidad) if id_entidad is not None else None,
                detalle=detalle[:255] if detalle else None,
                ip=(request.remote_addr or "")[:45] or None,
                resultado=resultado,
            )
            cadena = _bloquear_cadena()
            fila.hash_previo = cadena.ultimo_hash
            fila.hash_fila = calcular_huella(cadena.ultimo_hash, fila)
            cadena.ultimo_hash = fila.hash_fila
            cadena.total = (cadena.total or 0) + 1
            db.session.add(fila)
            db.session.commit()
            return
        except IntegrityError:
            # dos procesos intentaron crear a la vez la fila de la cadena: el segundo reintenta y ya la encuentra
            db.session.rollback()
            if intento == 2:
                current_app.logger.error("No se pudo escribir en la bitácora (%s): conflicto al crear la cadena", accion)
        except SQLAlchemyError as error:
            db.session.rollback()
            current_app.logger.error("No se pudo escribir en la bitácora (%s): %s", accion, error)
            return


# recorre la cadena de la bitácora y dice si alguna fila fue alterada o borrada directamente en la base de datos
def verificar_bitacora():
    """
    Devuelve un diccionario con:
      ok                   True si la cadena está íntegra
      verificadas          filas con huella revisadas
      sin_huella_previas   filas anteriores a la función de huellas (no se pueden verificar)
      problemas            lista de textos (vacía si todo está bien)
      total_registrado     cuántas filas encadenadas debería haber según la fila de la cadena
      ultimo_hash          huella de la última fila (conviene anotarla o conservarla junto al respaldo)
    """
    problemas = []
    esperado_previo = HUELLA_INICIAL
    verificadas = sin_huella_previas = 0
    ultimo_id = 0
    iniciada = False

    while True:
        lote = (
            Bitacora.query.filter(Bitacora.id_bitacora > ultimo_id).order_by(Bitacora.id_bitacora).limit(2000).all()
        )
        if not lote:
            break
        for fila in lote:
            ultimo_id = fila.id_bitacora
            if fila.hash_fila is None:
                if iniciada:
                    problemas.append(f"La fila {fila.id_bitacora} no tiene huella y está dentro de la cadena (¿insertada a mano?).")
                else:
                    sin_huella_previas += 1
                continue
            iniciada = True
            if fila.hash_previo != esperado_previo:
                problemas.append(
                    f"La fila {fila.id_bitacora} no encaja con la anterior (se borró o se movió alguna fila entre ambas)."
                )
            if calcular_huella(fila.hash_previo or "", fila) != fila.hash_fila:
                problemas.append(f"La fila {fila.id_bitacora} fue modificada: su contenido ya no coincide con su huella.")
            esperado_previo = fila.hash_fila
            verificadas += 1
        db.session.expire_all()

    cadena = db.session.get(BitacoraCadena, 1)
    total_registrado = cadena.total if cadena else 0
    if cadena is not None:
        if cadena.ultimo_hash != esperado_previo:
            problemas.append("La última fila de la bitácora no coincide con la huella guardada (se borraron filas finales).")
        if cadena.total != verificadas:
            problemas.append(f"Debería haber {cadena.total} filas encadenadas y hay {verificadas}.")
    elif verificadas:
        problemas.append("Falta la fila de control de la cadena.")

    return {
        "ok": not problemas, "verificadas": verificadas, "sin_huella_previas": sin_huella_previas,
        "problemas": problemas[:20], "total_registrado": total_registrado, "ultimo_hash": esperado_previo,
    }


# cuántas veces hizo este usuario esa acción en la ventana de tiempo (para limitar abusos, ej. exportaciones)
def acciones_recientes(accion, id_usuario, ventana_min):
    desde = datetime.now() - timedelta(minutes=ventana_min)
    return Bitacora.query.filter(
        Bitacora.accion == accion, Bitacora.id_usuario == id_usuario, Bitacora.fecha_hora > desde
    ).count()


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
