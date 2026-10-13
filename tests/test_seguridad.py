"""
Pruebas de los mecanismos de seguridad: autenticación, bloqueo por intentos,
bitácora de auditoría, control de acceso por rol y por propiedad del registro,
cabeceras HTTP, sesión y CSRF.
"""

import pytest

from app import create_app
from app.models import Bitacora
from app.seguridad import validar_contrasena
from conftest import CLAVE_ADMIN, CLAVE_TRABAJADOR, ConfigPruebas, ConfigPruebasConCSRF, iniciar_sesion


# cuenta cuántas filas de bitácora hay de una acción (y opcionalmente de un usuario)
def contar(app, accion, login=None, resultado=None):
    with app.app_context():
        consulta = Bitacora.query.filter_by(accion=accion)
        if login:
            consulta = consulta.filter_by(usuario_login=login)
        if resultado:
            consulta = consulta.filter_by(resultado=resultado)
        return consulta.count()


# registra una venta por el formulario real y devuelve (respuesta, id_venta o None)
def vender(cliente_http, datos, tipo="contado", cantidad=2):
    respuesta = cliente_http.post("/ventas/nueva", data={
        "id_cliente": datos["cliente"], "tipo_venta": tipo,
        "id_producto[]": [datos["p1"]], "cantidad[]": [cantidad], "precio_unitario[]": ["50"],
    })
    id_venta = None
    if respuesta.status_code == 302 and "/ventas/" in respuesta.headers["Location"]:
        id_venta = int(respuesta.headers["Location"].rstrip("/").split("/")[-1])
    return respuesta, id_venta


# ---------------------------------------------------------------------------
# Autenticación y bitácora
# ---------------------------------------------------------------------------
def test_login_correcto_redirige_y_queda_en_bitacora(app, cliente):
    antes = contar(app, "login_exitoso", "admin_t")
    r = iniciar_sesion(cliente, "admin_t", CLAVE_ADMIN)
    assert r.status_code == 302
    assert contar(app, "login_exitoso", "admin_t") == antes + 1


def test_login_incorrecto_da_mensaje_generico_y_se_audita(app, cliente):
    antes = contar(app, "login_fallido", "trab_a")
    r = iniciar_sesion(cliente, "trab_a", "clave-equivocada1")
    assert r.status_code == 401
    assert "Usuario o contraseña incorrectos".encode() in r.data
    assert contar(app, "login_fallido", "trab_a") == antes + 1


def test_usuario_inexistente_recibe_el_mismo_mensaje_que_clave_mala(cliente):
    r1 = iniciar_sesion(cliente, "no_existe_xyz", "cualquiera123")
    r2 = iniciar_sesion(cliente, "trab_a", "cualquiera123")
    assert r1.status_code == r2.status_code == 401
    assert b"Usuario o contrase" in r1.data and b"Usuario o contrase" in r2.data


def test_cuenta_inactiva_no_puede_entrar(app, cliente):
    r = iniciar_sesion(cliente, "inactivo_t", CLAVE_TRABAJADOR)
    assert r.status_code == 403
    assert contar(app, "login_cuenta_inactiva", "inactivo_t") >= 1


def test_bloqueo_tras_intentos_fallidos_incluso_con_clave_correcta(app, cliente):
    for _ in range(5):
        assert iniciar_sesion(cliente, "bloqueo_t", "mala-clave-123").status_code == 401
    r = iniciar_sesion(cliente, "bloqueo_t", CLAVE_TRABAJADOR)  # clave CORRECTA
    assert r.status_code == 429
    assert contar(app, "login_bloqueado", "bloqueo_t") >= 1
    # y no hubo sesión: sigue sin poder entrar a páginas protegidas
    assert cliente.get("/productos").status_code == 302


def test_un_login_exitoso_reinicia_el_conteo_de_fallos(app, cliente):
    for _ in range(4):
        iniciar_sesion(cliente, "reinicio_t", "mala-clave-123")
    assert iniciar_sesion(cliente, "reinicio_t", CLAVE_TRABAJADOR).status_code == 302
    cliente.post("/logout")
    for _ in range(4):
        assert iniciar_sesion(cliente, "reinicio_t", "mala-clave-123").status_code == 401
    # 4 + éxito + 4 no suman 5 seguidos: la cuenta NO debe estar bloqueada
    assert iniciar_sesion(cliente, "reinicio_t", CLAVE_TRABAJADOR).status_code == 302


def test_logout_se_audita_y_cierra_la_sesion(app, admin):
    antes = contar(app, "logout", "admin_t")
    admin.post("/logout")
    assert contar(app, "logout", "admin_t") == antes + 1
    assert admin.get("/usuarios").status_code == 302  # de vuelta al login


# ---------------------------------------------------------------------------
# Sesión y cabeceras
# ---------------------------------------------------------------------------
def test_cookie_de_sesion_es_httponly_y_samesite(cliente):
    r = iniciar_sesion(cliente, "admin_t", CLAVE_ADMIN)
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Expires=" in cookie  # sesión permanente: caduca por inactividad


def test_cabeceras_de_seguridad_presentes(cliente):
    r = cliente.get("/login")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"]
    assert r.headers["Cache-Control"] == "no-store"


def test_ninguna_plantilla_usa_scripts_en_linea(app, admin):
    # la CSP prohíbe scripts en línea: si alguna página los tuviera, dejarían de funcionar
    for ruta in ("/productos", "/usuarios", "/ventas/clientes", "/auditoria"):
        html = admin.get(ruta).get_data(as_text=True)
        assert "onsubmit=" not in html and "onclick=" not in html


def test_pagina_inexistente_muestra_404_amigable(admin):
    r = admin.get("/ruta/que/no/existe")
    assert r.status_code == 404
    assert "no encontrada".encode() in r.data


def test_sin_secret_key_la_app_no_arranca():
    class SinClave(ConfigPruebas):
        SECRET_KEY = None

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(SinClave)


# ---------------------------------------------------------------------------
# Control de acceso por rol (RNF-03 / CP-15)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ruta", [
    "/usuarios", "/usuarios/nuevo", "/auditoria", "/reportes", "/analitica", "/analitica/prediccion",
    "/analitica/segmentacion", "/dashboard", "/productos/nuevo", "/ventas",
])
def test_trabajador_no_accede_a_modulos_de_administrador(app, trabajador_a, ruta):
    antes = contar(app, "acceso_denegado")
    assert trabajador_a.get(ruta).status_code == 403
    assert contar(app, "acceso_denegado") == antes + 1


@pytest.mark.parametrize("ruta", ["/usuarios", "/auditoria", "/reportes", "/analitica", "/dashboard", "/ventas", "/productos"])
def test_sin_sesion_se_redirige_al_login(cliente, ruta):
    r = cliente.get(ruta)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_trabajador_no_puede_ejecutar_acciones_de_administrador(trabajador_a, datos):
    assert trabajador_a.post(f"/productos/{datos['p1']}/desactivar").status_code == 403
    assert trabajador_a.post("/usuarios/1/desactivar").status_code == 403


def test_menu_del_trabajador_no_muestra_modulos_de_administrador(trabajador_a):
    html = trabajador_a.get("/ventas/mis-ventas").get_data(as_text=True)
    assert "Mis ventas" in html
    for prohibido in ("Usuarios", "Auditoría", "Reportes", "Analítica comercial", "Ventas consolidadas"):
        assert prohibido not in html


# ---------------------------------------------------------------------------
# Control de acceso por propiedad del registro (un trabajador solo ve lo suyo)
# ---------------------------------------------------------------------------
def test_trabajador_solo_ve_el_detalle_de_sus_ventas(trabajador_a, trabajador_b, admin, datos):
    r, id_venta = vender(trabajador_a, datos)
    assert id_venta, r.get_data(as_text=True)[:300]
    assert trabajador_a.get(f"/ventas/{id_venta}").status_code == 200
    assert trabajador_b.get(f"/ventas/{id_venta}").status_code == 403
    assert admin.get(f"/ventas/{id_venta}").status_code == 200


def test_trabajador_no_ve_ni_paga_creditos_de_otro(app, trabajador_a, trabajador_b, admin, datos):
    _, id_venta = vender(trabajador_a, datos, tipo="credito", cantidad=1)
    with app.app_context():
        from app.models import Credito

        id_credito = Credito.query.filter_by(id_venta=id_venta).first().id_credito

    assert trabajador_a.get(f"/ventas/creditos/{id_credito}").status_code == 200
    assert trabajador_b.get(f"/ventas/creditos/{id_credito}").status_code == 403
    assert trabajador_b.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "10"}).status_code == 403

    # la lista de créditos del otro trabajador tampoco lo muestra
    assert f"/creditos/{id_credito}".encode() not in trabajador_b.get("/ventas/creditos").data
    assert f"/creditos/{id_credito}".encode() in trabajador_a.get("/ventas/creditos").data
    assert f"/creditos/{id_credito}".encode() in admin.get("/ventas/creditos").data


# ---------------------------------------------------------------------------
# Auditoría de acciones de negocio
# ---------------------------------------------------------------------------
def test_acciones_de_negocio_quedan_en_bitacora_con_su_autor(app, trabajador_a, admin, datos):
    antes_v = contar(app, "venta_registrada", "trab_a")
    vender(trabajador_a, datos)
    assert contar(app, "venta_registrada", "trab_a") == antes_v + 1

    antes_p = contar(app, "producto_desactivado", "admin_t")
    admin.post(f"/productos/{datos['p2']}/desactivar")
    admin.post(f"/productos/{datos['p2']}/reactivar")
    assert contar(app, "producto_desactivado", "admin_t") == antes_p + 1

    antes_u = contar(app, "usuario_creado", "admin_t")
    with app.app_context():
        from app.models import Rol, Sede

        rol = Rol.query.filter_by(nombre_rol="Trabajador").first().id_rol
        sede = Sede.query.first().id_sede
    r = admin.post("/usuarios/nuevo", data={
        "nombre": "Nuevo", "apellido": "Usuario", "usuario_login": "nuevo_aud", "id_rol": rol, "id_sede": sede,
        "contrasena": "ClaveSegura2026", "confirmar_contrasena": "ClaveSegura2026",
    })
    assert r.status_code == 302
    assert contar(app, "usuario_creado", "admin_t") == antes_u + 1


def test_el_administrador_puede_consultar_la_bitacora_con_filtros(admin):
    r = admin.get("/auditoria")
    assert r.status_code == 200 and "Auditoría de acciones".encode() in r.data
    r = admin.get("/auditoria?usuario=trab_a&accion=login_exitoso&resultado=exito&desde=2020-01-01&hasta=2099-12-31")
    assert r.status_code == 200
    # parámetros basura no rompen la pantalla
    assert admin.get("/auditoria?desde=no-es-fecha&pagina=abc&accion=inventada").status_code == 200


def test_la_exportacion_de_reportes_se_audita(app, admin):
    antes = contar(app, "reporte_exportado", "admin_t")
    r = admin.get("/reportes/exportar?tipo=ventas&desde=2026-01-01&hasta=2026-12-31")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert contar(app, "reporte_exportado", "admin_t") == antes + 1


# ---------------------------------------------------------------------------
# Política de contraseñas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clave,debe_fallar", [
    ("corta1", True), ("sololetrasaqui", True), ("123456789012", True), ("password1", True),
    ("Agrofalma2026x", False), ("Buena2026Clave", False),
])
def test_politica_de_contrasenas(clave, debe_fallar):
    assert bool(validar_contrasena(clave)) is debe_fallar


def test_contrasena_igual_al_usuario_es_rechazada():
    assert validar_contrasena("jperez2026", "JPerez2026")


# ---------------------------------------------------------------------------
# CSRF (con la protección activada, como en producción)
# ---------------------------------------------------------------------------
def test_post_sin_token_csrf_es_rechazado_y_auditado(app):
    app_csrf = create_app(ConfigPruebasConCSRF)
    c = app_csrf.test_client()
    r = c.post("/login", data={"usuario": "admin_t", "contrasena": CLAVE_ADMIN})
    assert r.status_code == 400
    assert "No se pudo procesar".encode() in r.data
    with app_csrf.app_context():
        assert Bitacora.query.filter_by(accion="csrf_rechazado").count() >= 1
