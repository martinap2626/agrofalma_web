"""
Consulta de la bitácora de auditoría — exclusivo del Administrador (RNF-03).

Es de solo lectura: ninguna pantalla permite editar ni borrar registros de
la bitácora, de modo que sirva como evidencia confiable de quién hizo qué.
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.models import Bitacora
from app.seguridad import registrar_accion, verificar_bitacora

POR_PAGINA = 10

# nombres legibles de cada acción, para que el Administrador no lea códigos internos
ETIQUETAS_ACCION = {
    "login_exitoso": "Inicio de sesión",
    "login_fallido": "Intento de acceso fallido",
    "login_bloqueado": "Acceso bloqueado por intentos fallidos",
    "login_cuenta_inactiva": "Intento de acceso con cuenta desactivada",
    "logout": "Cierre de sesión",
    "parametros_editados": "Parámetros del sistema modificados",
    "consola_respaldo": "Consola: respaldo de la base",
    "consola_restauracion": "Consola: restauración de la base",
    "consola_importar_historico": "Consola: importación de ventas históricas",
    "consola_cargar_catalogo": "Consola: carga del catálogo",
    "consola_cargar_inventario": "Consola: carga de inventario",
    "consola_actualizar_analitica": "Consola: actualización de analítica",
    "consola_preparar_bd": "Consola: preparación de la base",
    "consola_seed_passwords": "Consola: restablecer contraseñas iniciales",
    "consola_eventos_ejemplo": "Consola: eventos de ejemplo",
    "venta_registrada": "Venta registrada",
    "pago_registrado": "Pago de crédito registrado",
    "cliente_creado": "Cliente creado",
    "cliente_editado": "Cliente editado",
    "cliente_desactivado": "Cliente desactivado",
    "cliente_reactivado": "Cliente reactivado",
    "producto_creado": "Producto creado",
    "producto_editado": "Producto editado",
    "producto_desactivado": "Producto desactivado",
    "producto_reactivado": "Producto reactivado",
    "usuario_creado": "Usuario creado",
    "usuario_editado": "Usuario editado",
    "usuario_desactivado": "Usuario desactivado",
    "usuario_reactivado": "Usuario reactivado",
    "evento_creado": "Evento del negocio registrado",
    "evento_editado": "Evento del negocio editado",
    "evento_eliminado": "Evento del negocio eliminado",
    "reporte_exportado": "Reporte exportado a CSV",
    "exportacion_limitada": "Descarga de reporte bloqueada por exceso de descargas",
    "acceso_denegado": "Acceso denegado (sin permiso)",
    "csrf_rechazado": "Formulario rechazado (token inválido)",
    "venta_anulada": "Venta anulada",
    "venta_precio_rebajado": "Venta con precio por debajo del de lista",
    "inventario_ingreso": "Ingreso de mercadería al inventario",
    "inventario_conteo": "Conteo físico de inventario",
    "contrasena_cambiada": "Contraseña cambiada por el propio usuario",
    "contrasena_fallida": "Cambio de contraseña con la contraseña actual incorrecta",
    "contrasena_bloqueada": "Cambio de contraseña bloqueado por intentos fallidos",
    "bitacora_verificada": "Verificación de la integridad de la bitácora",
}

bp = Blueprint("auditoria", __name__, url_prefix="/auditoria")


# convierte "YYYY-MM-DD" en fecha, o None si viene vacío o mal escrito
def _fecha(texto):
    try:
        return date.fromisoformat(texto)
    except (TypeError, ValueError):
        return None


# lista la bitácora con filtros por fecha, usuario, acción y resultado, paginada de a 50
@bp.route("")
@login_required
@rol_requerido("Administrador")
def listar():
    desde = _fecha(request.args.get("desde"))
    hasta = _fecha(request.args.get("hasta"))
    usuario = request.args.get("usuario", "").strip()
    accion = request.args.get("accion", "").strip()
    resultado = request.args.get("resultado", "").strip()
    try:
        pagina = max(int(request.args.get("pagina", "1")), 1)
    except ValueError:
        pagina = 1

    contexto = {
        "desde": desde, "hasta": hasta, "usuario": usuario, "accion": accion,
        "resultado": resultado, "etiquetas": ETIQUETAS_ACCION, "pagina": pagina,
        "registros": [], "total": 0, "total_paginas": 1,
    }

    try:
        consulta = Bitacora.query
        if desde:
            consulta = consulta.filter(Bitacora.fecha_hora >= datetime.combine(desde, datetime.min.time()))
        if hasta:
            consulta = consulta.filter(
                Bitacora.fecha_hora < datetime.combine(hasta + timedelta(days=1), datetime.min.time())
            )
        if usuario:
            consulta = consulta.filter(Bitacora.usuario_login.ilike(f"%{usuario}%"))
        if accion in ETIQUETAS_ACCION:
            consulta = consulta.filter(Bitacora.accion == accion)
        if resultado in ("exito", "fallo", "denegado"):
            consulta = consulta.filter(Bitacora.resultado == resultado)

        total = consulta.count()
        total_paginas = max((total + POR_PAGINA - 1) // POR_PAGINA, 1)
        pagina = min(pagina, total_paginas)
        registros = (
            consulta.order_by(Bitacora.fecha_hora.desc(), Bitacora.id_bitacora.desc())
            .offset((pagina - 1) * POR_PAGINA).limit(POR_PAGINA).all()
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("consultar bitácora", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("auditoria/listar.html", **contexto), 503

    contexto.update(registros=registros, total=total, total_paginas=total_paginas, pagina=pagina)
    return render_template("auditoria/listar.html", **contexto)


# comprueba que nadie haya editado ni borrado filas de la bitácora directamente en la base de datos (cadena de huellas)
@bp.route("/verificar", methods=["POST"])
@login_required
@rol_requerido("Administrador")
def verificar():
    try:
        resultado = verificar_bitacora()
    except SQLAlchemyError as error:
        registrar_error_conexion("verificar la bitácora", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("auditoria.listar"))

    if resultado["ok"]:
        registrar_accion("bitacora_verificada", detalle=f"Íntegra: {resultado['verificadas']} filas verificadas")
        flash(
            f"La bitácora está íntegra: se verificaron {resultado['verificadas']} registros encadenados y no hay ninguno "
            "alterado ni faltante.", "success",
        )
    else:
        registrar_accion(
            "bitacora_verificada", detalle=f"ALTERADA: {len(resultado['problemas'])} problema(s)", resultado="fallo",
        )
        flash("¡Atención! La bitácora fue alterada fuera del sistema. Avisa a quien administra el servidor.", "danger")
        for problema in resultado["problemas"][:5]:
            flash(problema, "danger")
    return redirect(url_for("auditoria.listar"))
